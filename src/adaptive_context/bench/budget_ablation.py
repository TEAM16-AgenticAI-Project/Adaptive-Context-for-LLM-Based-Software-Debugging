"""Budget ablation on dataset A (generated call-chain bugs) with the offline simulator.

Arms (all use the same retriever, sequencing and SimulatedLLM):
  legacy     code-only budget, old packing                    = main before Milestone 2
  old_pack   whole-prompt budget, old packing (requests compressible, newest dropped first on ties)
  new        whole-prompt budget, pinned requests (>= trimmed), oldest request dropped first
  new_notes  as new, plus two-line notes for evicted pinned code

Numbers are SIMULATOR numbers: they measure context management, not model quality.

  python -m adaptive_context.bench.budget_ablation --n 300 --out results
  python -m adaptive_context.bench.budget_ablation --trace 17 --budgets 600 --out results   # traces of one case
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

from ..adaptive import AdaptiveDebugger
from ..chunking import chunk_repo
from ..retrieval import BM25Retriever
from ..sim import SimulatedLLM
from .generate import GenParams, generate_case
from .run import evaluate, wilson

ARMS = {"legacy": {"pin_policy": "legacy", "budget_scope": "code"},
        "old_pack": {"pin_policy": "legacy", "budget_scope": "prompt"},
        "new": {"pin_policy": "pinned", "budget_scope": "prompt"},
        "new_notes": {"pin_policy": "notes", "budget_scope": "prompt"}}
SYSTEMS = {"adaptive": 0, "adaptive_graph": 2}           # name -> prefetch depth
BUDGETS = (600, 800, 1000, 1500, 3000)


def revision() -> dict:
    def git(*args):
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""
    return {"commit": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no", "src"))}


def _row(case, res) -> dict:
    ev = evaluate(case, res)
    level = dict(res.context_items).get(case.truth.chunk_id)
    return {**ev, "stop": res.stop_reason, "over_rounds": res.stats.over_budget_rounds,
            "max_prompt": max((t.get("prompt_tokens", 0) for t in res.trace), default=0),
            "bug_level": level, "pinned_dropped": sum(len(t.get("dropped_pinned", [])) for t in res.trace)}


def _work(job):
    seed, budgets, gp, rounds = job
    case = generate_case(seed, gp)
    retr = BM25Retriever(chunk_repo(case.files))
    llm = SimulatedLLM(0.0)
    out = {"seed": seed, "depth": case.truth.depth}
    for b in budgets:
        for arm, kw in ARMS.items():
            for sysname, pd in SYSTEMS.items():
                res = AdaptiveDebugger(retr, llm, initial_k=3, max_rounds=rounds, token_budget=b,
                                       prefetch_depth=pd, **kw).run(case.bug_report)
                out[f"{b}/{arm}/{sysname}"] = _row(case, res)
    return out


def summarize(rows, budgets) -> dict:
    n = len(rows)
    mean = lambda xs: sum(xs) / max(len(xs), 1)       # noqa: E731
    table = {}
    for b in budgets:
        for arm in ARMS:
            for sysname in SYSTEMS:
                rs = [r[f"{b}/{arm}/{sysname}"] for r in rows]
                k = sum(r["func_hit"] for r in rs)
                table[f"{b}/{arm}/{sysname}"] = {
                    "budget": b, "arm": arm, "system": sysname, "n": n,
                    "func_acc": k / n, "func_ci95": list(wilson(k, n)), "grounded": mean([r["grounded"] for r in rs]),
                    "evidence_recall": mean([r["evidence"] for r in rs]), "fix_ok": mean([r["fix_ok"] for r in rs]),
                    "func_hit_from_note": mean([r["func_hit"] and r["bug_level"] == 4 for r in rs]),
                    "tokens": mean([r["tokens"] for r in rs]), "calls": mean([r["calls"] for r in rs]),
                    "max_prompt_tokens": max(r["max_prompt"] for r in rs),
                    "runs_over_budget": mean([r["over_rounds"] > 0 for r in rs]),
                    "pinned_evictions": mean([r["pinned_dropped"] for r in rs]),
                    "stop_reasons": dict(Counter(r["stop"] for r in rs))}
    return table


def print_table(table) -> None:
    hdr = (f"{'budget':>6} {'arm':<10}{'system':<15}{'func_acc':>9}{'grounded':>9}{'fix_ok':>8}{'tokens':>8}"
           f"{'calls':>6}{'maxprompt':>10}{'over%':>7}")
    print(hdr + "\n" + "-" * len(hdr))
    for m in table.values():
        print(f"{m['budget']:>6} {m['arm']:<10}{m['system']:<15}{m['func_acc']:>9.3f}{m['grounded']:>9.3f}"
              f"{m['fix_ok']:>8.3f}{m['tokens']:>8.0f}{m['calls']:>6.2f}{m['max_prompt_tokens']:>10}"
              f"{100 * m['runs_over_budget']:>6.1f}%")


def trace_case(seed, budgets, gp, rounds) -> dict:
    """Full per-round traces of one case for every arm (for the Section 5 failure write-up)."""
    case = generate_case(seed, gp)
    retr = BM25Retriever(chunk_repo(case.files))
    out = {"seed": seed, "bug_report": case.bug_report, "evaluator_only_truth": vars(case.truth), "runs": {}}
    for b in budgets:
        for arm, kw in ARMS.items():
            for sysname, pd in SYSTEMS.items():
                res = AdaptiveDebugger(retr, SimulatedLLM(0.0), initial_k=3, max_rounds=rounds, token_budget=b,
                                       prefetch_depth=pd, **kw).run(case.bug_report)
                out["runs"][f"{b}/{arm}/{sysname}"] = {"diagnosis": vars(res.diagnosis), "stop_reason": res.stop_reason,
                                                       "score": _row(case, res), "trace": res.trace}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Budget ablation (simulator, dataset A)")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--budgets", default=",".join(map(str, BUDGETS)))
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--trace", type=int, default=None, help="dump full traces for this seed instead of the ablation")
    ap.add_argument("--out", default="results")
    a = ap.parse_args(argv)
    budgets = [int(b) for b in a.budgets.split(",")]
    gp = GenParams()
    os.makedirs(a.out, exist_ok=True)
    meta = {"args": vars(a), "revision": revision(), "llm": "SimulatedLLM(miss_rate=0)", "dataset": "A (generated)",
            "gen_params": vars(gp), "note": "simulator numbers, not model quality"}
    if a.trace is not None:
        path = os.path.join(a.out, f"budget_trace_seed{a.trace}.json")
        with open(path, "w") as fh:
            json.dump({**meta, **trace_case(a.trace, budgets, gp, a.rounds)}, fh, indent=1)
        print(f"saved: {path}")
        return
    jobs = [(a.seed + i, budgets, gp, a.rounds) for i in range(a.n)]
    t0 = time.time()
    if a.workers <= 1:
        rows = [_work(j) for j in jobs]
    else:
        with ProcessPoolExecutor(a.workers) as ex:
            rows = list(ex.map(_work, jobs, chunksize=max(1, a.n // (a.workers * 8))))
    table = summarize(rows, budgets)
    print_table(table)
    print(f"\nwall time: {time.time() - t0:.1f}s")
    with open(os.path.join(a.out, "budget_ablation.json"), "w") as fh:
        json.dump({**meta, "table": table}, fh, indent=1)
    with open(os.path.join(a.out, "budget_ablation_cases.jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"saved: {a.out}/budget_ablation.json, {a.out}/budget_ablation_cases.jsonl")


if __name__ == "__main__":
    main()
