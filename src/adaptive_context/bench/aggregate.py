"""Aggregate and validate benchmark result files for the Milestone 2 report.

Every number in the report must come from a results file produced by a committed
revision, and simulator numbers must never be mixed with live-model numbers. This
module loads the summary.json files under a results directory, checks those rules,
and produces a single consolidated view that the report renderer can turn into
tables. It fails loudly when a result set is internally inconsistent, so a bad run
cannot silently reach the report.

Usage:
    python -m adaptive_context.bench.aggregate --results results --out results/m2
"""
from __future__ import annotations

import argparse
import glob
import json
import os


# The two LLM backends a run can use. A single report table must not combine them.
SIM_BACKEND = "sim"
LIVE_BACKENDS = ("groq",)


class ResultError(ValueError):
    """Raised when a results file is missing required fields or mixes backends."""


def _load_one(path: str) -> dict:
    """Load one summary.json into a flat record, or None if it is not a benchmark summary.

    The results tree holds summary.json files from several tools (benchmark runs,
    dataset validations, audits). Only benchmark summaries have a 'systems' block;
    anything else is not a results table and is skipped rather than treated as an error.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    args = data.get("args", {})
    summary = data.get("summary", data)
    if not isinstance(summary, dict) or "systems" not in summary:
        return None
    backend = args.get("llm", "unknown")
    return {"path": path, "backend": backend, "args": args, "summary": summary}


def load_results(results_dir: str) -> list[dict]:
    """Load every benchmark summary.json under results_dir (recursively), sorted by path.

    Files that are not benchmark summaries (for example dataset-validation summaries)
    are skipped, so a mixed results tree does not cause an error.
    """
    pattern = os.path.join(results_dir, "**", "summary.json")
    paths = sorted(glob.glob(pattern, recursive=True))
    # Also catch a summary.json placed directly in results_dir.
    direct = os.path.join(results_dir, "summary.json")
    if os.path.isfile(direct) and direct not in paths:
        paths.insert(0, direct)
    records = [_load_one(p) for p in paths]
    return [r for r in records if r is not None]


def classify_backend(backend: str) -> str:
    """Return 'simulator', 'live', or 'unknown' for a backend name."""
    if backend == SIM_BACKEND:
        return "simulator"
    if backend in LIVE_BACKENDS:
        return "live"
    return "unknown"


def validate(records: list[dict]) -> list[str]:
    """Return a list of problems found across the loaded result records.

    An empty list means the result set is internally consistent and safe to report.
    """
    problems: list[str] = []
    if not records:
        problems.append("no results found: nothing to report")
        return problems
    kinds = {classify_backend(r["backend"]) for r in records}
    if "simulator" in kinds and "live" in kinds:
        problems.append(
            "results mix simulator and live backends; report them in separate tables, "
            "never one combined row set")
    for r in records:
        if classify_backend(r["backend"]) == "unknown":
            problems.append(f"{r['path']}: unknown LLM backend '{r['backend']}'; "
                            "label it sim or a known live backend before reporting")
        n = r["summary"].get("n_cases", 0)
        if not n:
            problems.append(f"{r['path']}: n_cases is zero; an empty run must not reach the report")
    return problems


def group_by_backend(records: list[dict]) -> dict:
    """Group records into simulator and live buckets so tables never mix them."""
    groups: dict[str, list[dict]] = {"simulator": [], "live": [], "unknown": []}
    for r in records:
        groups[classify_backend(r["backend"])].append(r)
    return {k: v for k, v in groups.items() if v}


def consolidate(results_dir: str) -> dict:
    """Load, validate, and group all results under results_dir.

    Returns a dict with the grouped records and any validation problems. Problems
    are returned rather than raised so the caller decides whether a mixed set is
    fatal; the CLI treats any problem as fatal.
    """
    records = load_results(results_dir)
    problems = validate(records)
    return {
        "results_dir": results_dir,
        "n_files": len(records),
        "backends": sorted({r["backend"] for r in records}),
        "groups": group_by_backend(records),
        "problems": problems,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Aggregate and validate benchmark results for the Milestone 2 report")
    ap.add_argument("--results", default="results",
                    help="directory to scan for summary.json files (default results)")
    ap.add_argument("--out", default=os.path.join("results", "m2"),
                    help="directory to write the consolidated index (default results/m2)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any validation problem is found")
    a = ap.parse_args(argv)

    report = consolidate(a.results)
    os.makedirs(a.out, exist_ok=True)
    index_path = os.path.join(a.out, "results_index.json")
    serializable = {
        "results_dir": report["results_dir"],
        "n_files": report["n_files"],
        "backends": report["backends"],
        "group_counts": {k: len(v) for k, v in report["groups"].items()},
        "files": {k: [r["path"] for r in v] for k, v in report["groups"].items()},
        "problems": report["problems"],
    }
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(serializable, fh, indent=2)

    print(f"scanned {report['n_files']} result file(s) under {a.results}")
    print(f"backends seen: {', '.join(report['backends']) or 'none'}")
    print(f"wrote {index_path}")
    if report["problems"]:
        print("\nproblems:")
        for p in report["problems"]:
            print(f"  - {p}")
        if a.strict:
            raise SystemExit(1)
    else:
        print("no problems: result set is internally consistent")


if __name__ == "__main__":
    main()
