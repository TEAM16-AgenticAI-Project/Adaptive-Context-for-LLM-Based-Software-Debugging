"""Name ambiguity on dataset B (real BugsInPy repos): callee candidates per call site, before and after
import-aware resolution.

"Before" is the old rule: every function/method whose short name equals the called name. "After" is
`BM25Retriever.resolve_call` (same-file definition, then the file's imports, then the old rule as a fallback).
Only calls with at least one candidate before are counted (calls into the repository). No model is involved.

  python -m adaptive_context.bench.name_ambiguity --manifest data/real/thefuck.jsonl --out results
"""
from __future__ import annotations

import argparse
import json
import os
import statistics

from ..cases import load_cases
from ..chunking import chunk_repo, load_repo_dir
from ..retrieval import BM25Retriever
from .budget_ablation import revision


def _is_test(path: str) -> bool:
    return path.startswith(("tests/", "test/")) or "/tests/" in path or os.path.basename(path).startswith("test_")


def call_stats(retr: BM25Retriever) -> list:
    """One (before, after, is_test, resolved_by_import_or_file) tuple per call site form."""
    out = []
    for ch in retr.chunks:
        if ch.kind not in ("function", "method", "module"):
            continue
        imports = retr.imports_of(ch)
        for name in retr.call_names(ch, imports):
            before = len(retr.name_matches(ch, name))
            if before == 0:
                continue
            after = len(retr.resolve_call(ch, name, imports))
            out.append((before, after, _is_test(ch.path)))
    return out


def _summary(rows) -> dict:
    if not rows:
        return {"calls": 0}
    b, a = [r[0] for r in rows], [r[1] for r in rows]
    return {"calls": len(rows), "median_before": statistics.median(b), "median_after": statistics.median(a),
            "mean_before": sum(b) / len(b), "mean_after": sum(a) / len(a),
            "unique_before": sum(x == 1 for x in b) / len(b), "unique_after": sum(x == 1 for x in a) / len(a),
            "max_before": max(b), "max_after": max(a)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Callee candidates per call, before/after import-aware resolution")
    ap.add_argument("--manifest", default="data/real/thefuck.jsonl")
    ap.add_argument("--out", default="results")
    a = ap.parse_args(argv)
    per_case, all_rows = {}, []
    for case in load_cases(a.manifest):
        if not os.path.isdir(case.repo):
            raise SystemExit(f"missing {case.repo}: run scripts/setup_env.sh first")
        retr = BM25Retriever(chunk_repo(load_repo_dir(case.repo)))
        rows = call_stats(retr)
        all_rows.extend(rows)
        per_case[case.id] = {**_summary(rows), "chunks": len(retr.chunks), "functions_named_match":
                             sum(c.short_name == "match" and c.kind in ("function", "method") for c in retr.chunks)}
        print(f"{case.id}: {per_case[case.id]['calls']} calls, median {per_case[case.id]['median_before']} -> "
              f"{per_case[case.id]['median_after']}")
    result = {"manifest": a.manifest, "revision": revision(),
              "all_calls": _summary(all_rows), "test_code_calls": _summary([r for r in all_rows if r[2]]),
              "source_code_calls": _summary([r for r in all_rows if not r[2]]),
              "median_of_case_medians": {
                  "before": statistics.median(v["median_before"] for v in per_case.values()),
                  "after": statistics.median(v["median_after"] for v in per_case.values())},
              "per_case": per_case}
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "name_ambiguity.json")
    with open(path, "w") as fh:
        json.dump(result, fh, indent=1)
    print(json.dumps({k: result[k] for k in ("all_calls", "test_code_calls", "source_code_calls")}, indent=1))
    print(f"saved: {path}")


if __name__ == "__main__":
    main()
