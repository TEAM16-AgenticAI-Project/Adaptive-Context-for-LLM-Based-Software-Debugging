"""Run the chosen systems on a case manifest and write a comparable results table.

This is the link between the datasets (built and validated by controlled.py and the
real-bug manifests) and the systems (built by systems.make_system). For each case it
runs every selected system on the same bug report and repository, scores the diagnosis
against the evaluator-only gold labels with cases.score_diagnosis, and records one row
per (case, system). It writes results.jsonl, summary.json, and table.md.

The agent systems get their tools on the case's repository (and agent_verify its fix check, multifix.TestOracle
with the case's test command). With --check-fixes every system's final fix is applied in a temporary copy and the
case's failing test plus the related tests are run, the same check for baselines and proposed systems, so the
table also reports how often the proposed fix actually passes the tests. Gold labels are never shown to a system.

The proposed system is never counted as its own baseline; the baselines are the static
configurations. Scoring uses gold labels that are never shown to a system.

Usage:
    python -m adaptive_context.bench.run_cases --manifest data/controlled/cases.jsonl \
        --systems static_k3,static_k10,adaptive,adaptive_graph --llm sim --out results/m2/controlled
"""
from __future__ import annotations

import argparse
import json
import os
import time

from ..cases import load_cases, score_diagnosis
from ..chunking import chunk_repo
from ..retrieval import BM25Retriever
from ..sim import SimulatedLLM
from .systems import BASELINE_NAMES, available_systems, make_system, result_row

AGENT_SYSTEMS = ("agent", "agent_verify")


def _read_repo_files(repo_dir: str) -> dict:
    """Read every .py file under repo_dir into a {relative_path: text} mapping."""
    files = {}
    for root, _dirs, names in os.walk(repo_dir):
        for name in names:
            if not name.endswith(".py"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, repo_dir).replace("\\", "/")
            try:
                with open(full, encoding="utf-8") as fh:
                    files[rel] = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
    return files


def _make_llm(kind: str, model: str | None, miss: float):
    """Build the LLM backend. 'sim' needs no network; 'groq' needs GROQ_API_KEY."""
    if kind == "sim":
        return SimulatedLLM(miss)
    if kind == "groq":
        from ..llm import GroqLLM
        return GroqLLM(model=model)
    raise ValueError(f"unknown llm backend '{kind}'")


def run_case(case, system_names, llm, cfg, *, agent_llm=None, python=None, check_fixes=False) -> list:
    """Run every named system on one case and return a list of scored rows.

    The same chunked repository and retriever are shared across systems so the only
    variable between rows is the system itself. agent_llm is the tool-calling LLM for the agent systems (the same
    model); python runs the case's tests (its environment); check_fixes adds fix_passed to every row.
    """
    files = _read_repo_files(case.repo)
    chunks = chunk_repo(files)
    retriever = BM25Retriever(chunks)
    oracle = None
    if case.test_cmd and (check_fixes or "agent_verify" in system_names):
        from ..multifix import TestOracle
        oracle = TestOracle(case.repo, python=python, test_cmd=case.test_cmd)
    rows = []
    for name in system_names:
        if name in AGENT_SYSTEMS:
            from ..tools import Tools
            tools = Tools(retriever, case.repo, python=python, test_cmd=case.test_cmd or None)
            verifier = oracle.check if name == "agent_verify" and oracle else None
            system = make_system(name, retriever, agent_llm or llm, cfg, tools=tools, verifier=verifier)
        else:
            system = make_system(name, retriever, llm, cfg)
        started = time.perf_counter()
        result = system.run(case.bug_report)
        elapsed = time.perf_counter() - started
        row = result_row(name, result)
        score = score_diagnosis(case, result.diagnosis, result.context_items)
        row.update({
            "case": case.id,
            "dataset": case.dataset,
            "file_hit": score["file_hit"],
            "func_hit": score["func_hit"],
            "evidence": score["evidence"],
            "grounded": score["grounded"],
            "elapsed_s": elapsed,
            "stop_reason": result.stop_reason,
            "gold_files": list(case.gold_files),          # evaluator-only, for the report; never shown to a system
        })
        if check_fixes and oracle is not None:
            d = result.diagnosis
            if name == "agent_verify" and result.verification:
                check = result.verification                   # its own loop already ran this check on this fix
            elif d.valid and (d.fix or "").strip():
                check = oracle.check(d)
            else:
                check = {"passed": False, "reason": "no_fix"}
            row.update(fix_passed=bool(check.get("passed")), fix_target_passed=bool(check.get("target_passed")),
                       fix_reason=check.get("reason", ""), fix_files=check.get("files_changed", []))
        rows.append(row)
    return rows


def summarize(rows, system_names) -> dict:
    """Aggregate per-system means across all cases into a summary dict."""
    n_cases = len({r["case"] for r in rows})

    def mean(xs):
        return (sum(xs) / len(xs)) if xs else 0.0

    systems = {}
    for name in system_names:
        rs = [r for r in rows if r["system"] == name]
        if not rs:
            continue
        systems[name] = {
            "role": "baseline" if name in BASELINE_NAMES else "proposed",
            "n": len(rs),
            "file_acc": mean([r["file_hit"] for r in rs]),
            "func_acc": mean([r["func_hit"] for r in rs]),
            "grounded": mean([r["grounded"] for r in rs]),
            "tokens": mean([r["total_tokens"] for r in rs]),
            "llm_calls": mean([r["llm_calls"] for r in rs]),
            "wall_s": mean([r["wall_s"] for r in rs]),
            "failures": sum(r["failures"] for r in rs),
        }
        checked = [r for r in rs if "fix_passed" in r]
        if checked:
            systems[name]["fix_rate"] = mean([r["fix_passed"] for r in checked])
            systems[name]["target_rate"] = mean([r["fix_target_passed"] for r in checked])
    return {"n_cases": n_cases, "systems": systems}


def to_markdown(summary: dict, backend: str) -> str:
    """Render the summary as a Markdown table for Milestone 2 Section 3."""
    fixes = any("fix_rate" in m for m in summary["systems"].values())
    head = "| System | Role | File acc | Func acc | Grounded |" + (" Fix passes tests |" if fixes else "")
    lines = [f"Backend: {backend}. Cases: {summary['n_cases']}.", "",
             head + " Tokens | LLM calls | Wall (s) | Failures |",
             "| --- " * (9 + fixes) + "|"]
    for name, m in summary["systems"].items():
        fix = (f" {m['fix_rate']:.3f} |" if "fix_rate" in m else " n/a |") if fixes else ""
        lines.append(
            f"| {name} | {m['role']} | {m['file_acc']:.3f} | {m['func_acc']:.3f} | "
            f"{m['grounded']:.3f} |{fix} {m['tokens']:.0f} | {m['llm_calls']:.2f} | "
            f"{m['wall_s']:.2f} | {m['failures']} |")
    return "\n".join(lines) + "\n"


def write_outputs(rows, summary, backend, out_dir, model=None):
    """Write results.jsonl, summary.json, and table.md into out_dir."""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "results.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump({"args": {"llm": backend, "model": model}, "summary": summary}, fh, indent=2)
    with open(os.path.join(out_dir, "table.md"), "w", encoding="utf-8") as fh:
        fh.write(to_markdown(summary, backend))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run selected systems on a case manifest and score them")
    ap.add_argument("--manifest", required=True, help="path to a JSONL case manifest")
    ap.add_argument("--systems", default="static_k3,static_k10,adaptive,adaptive_graph",
                    help="comma-separated system names (default: the four core systems)")
    ap.add_argument("--llm", choices=["sim", "groq"], default="sim")
    ap.add_argument("--model", default=None)
    ap.add_argument("--miss", type=float, default=0.0, help="sim: probability of overlooking the mismatch")
    ap.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    ap.add_argument("--cases", default=None, help="comma-separated case ids to run (default: all)")
    ap.add_argument("--out", default=os.path.join("results", "m2", "cases"))
    ap.add_argument("--check-fixes", action="store_true",
                    help="apply every system's fix in a temporary copy and run the case's tests (fix_passed)")
    ap.add_argument("--env", action="append", default=[], help="NAME=PYTHON: the Python that runs a case's tests")
    ap.add_argument("--python", default=None, help="Python for cases whose env is not given with --env")
    ap.add_argument("--max-tokens", type=int, default=4096, help="agent systems: output tokens per LLM call")
    ap.add_argument("--rounds", type=int, default=None, help="rounds for the adaptive and agent systems")
    ap.add_argument("--budget", type=int, default=None, help="context token budget")
    ap.add_argument("--max-verify", type=int, default=None, help="agent_verify: fix checks after the first")
    a = ap.parse_args(argv)

    requested = [s.strip() for s in a.systems.split(",") if s.strip()]
    buildable = set(available_systems(include_agent=True))
    system_names = [s for s in requested if s in buildable]
    skipped = [s for s in requested if s not in buildable]
    if skipped:
        print(f"skipping systems not buildable here: {', '.join(skipped)}")
    if not system_names:
        raise SystemExit("no buildable systems requested")

    cases = load_cases(a.manifest)
    if a.cases:
        wanted = [c.strip() for c in a.cases.split(",") if c.strip()]
        cases = [c for c in cases if c.id in wanted]
    if a.limit:
        cases = cases[:a.limit]
    llm = _make_llm(a.llm, a.model, a.miss)
    agent_llm = None
    if any(s in AGENT_SYSTEMS for s in system_names):
        from ..llm import make_llm
        agent_llm = make_llm(a.llm, model=a.model, max_tokens=a.max_tokens, tools=True)
    envs = dict(e.split("=", 1) for e in a.env)

    cfg = {k: v for k, v in (("rounds", a.rounds), ("budget", a.budget), ("max_verify", a.max_verify))
           if v is not None}
    all_rows = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case.id}", flush=True)
        all_rows.extend(run_case(case, system_names, llm, cfg, agent_llm=agent_llm,
                                 python=envs.get(case.env) or a.python, check_fixes=a.check_fixes))
        write_outputs(all_rows, summarize(all_rows, system_names), a.llm, a.out,  # partial results survive
                      model=getattr(llm, "model", None))
    summary = summarize(all_rows, system_names)
    write_outputs(all_rows, summary, a.llm, a.out, model=getattr(llm, "model", None))
    print(to_markdown(summary, a.llm))
    print(f"wrote {a.out}/results.jsonl, summary.json, table.md")


if __name__ == "__main__":
    main()
