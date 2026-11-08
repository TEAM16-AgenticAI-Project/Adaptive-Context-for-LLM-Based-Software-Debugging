"""Benchmark runner: compares systems on N generated cases (parallel, deterministic)."""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor

from ..adaptive import AdaptiveDebugger
from ..baseline import StaticRAG
from ..chunking import chunk_repo
from ..llm import GroqLLM
from ..retrieval import BM25Retriever
from ..sim import SimulatedLLM
from .generate import GenParams, generate_case, verify_fix

SYSTEMS = ("static_k3", "static_wide", "adaptive", "adaptive_graph")


def make_systems(retr, llm, cfg):
    return {
        "static_k3": StaticRAG(retr, llm, top_k=cfg["top_k"]),
        "static_wide": StaticRAG(retr, llm, top_k=cfg["wide_k"]),
        "adaptive": AdaptiveDebugger(retr, llm, initial_k=cfg["top_k"], max_rounds=cfg["rounds"],
                                     token_budget=cfg["budget"], prefetch_depth=0),
        "adaptive_graph": AdaptiveDebugger(retr, llm, initial_k=cfg["top_k"], max_rounds=cfg["rounds"],
                                           token_budget=cfg["budget"], prefetch_depth=2),
    }


def evaluate(case, res) -> dict:
    d, t = res.diagnosis, case.truth
    file_hit = d.valid and d.file == t.file
    func_hit = file_hit and d.function == t.function
    evidence = any(cid == t.chunk_id and lvl <= 2 for cid, lvl in res.context_items)
    s = res.stats
    return {"file_hit": file_hit, "func_hit": func_hit, "grounded": func_hit and evidence, "evidence": evidence,
            "fix_ok": bool(func_hit and verify_fix(case, d.file, d.function, d.fix)),
            "in_tok": s.input_tokens, "out_tok": s.output_tokens, "tokens": s.total_tokens,
            "calls": s.llm_calls, "retrievals": s.retrieval_calls, "rounds": s.rounds,
            "latency": s.latency_s, "failures": s.failures}


def _work(job):
    seed, gp, cfg = job
    case = generate_case(seed, gp)
    chunks = chunk_repo(case.files)
    retr = BM25Retriever(chunks)
    llm = GroqLLM(model=cfg.get("model")) if cfg["llm"] == "groq" else SimulatedLLM(cfg["miss"])
    row = {"seed": seed, "depth": case.truth.depth, "chunks": len(chunks)}
    for name, system in make_systems(retr, llm, cfg).items():
        row[name] = evaluate(case, system.run(case.bug_report))
    return row


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def summarize(rows) -> dict:
    n = len(rows)
    out = {"n_cases": n, "avg_chunks": sum(r["chunks"] for r in rows) / max(n, 1), "systems": {}, "by_depth": {}}
    mean = lambda xs: sum(xs) / max(len(xs), 1)
    for s in SYSTEMS:
        rs = [r[s] for r in rows]
        k = sum(r["func_hit"] for r in rs)
        lo, hi = wilson(k, n)
        out["systems"][s] = {
            "func_acc": k / n, "func_ci95": [lo, hi], "file_acc": mean([r["file_hit"] for r in rs]),
            "grounded": mean([r["grounded"] for r in rs]), "evidence_recall": mean([r["evidence"] for r in rs]),
            "fix_ok": mean([r["fix_ok"] for r in rs]), "tokens": mean([r["tokens"] for r in rs]),
            "calls": mean([r["calls"] for r in rs]), "retrievals": mean([r["retrievals"] for r in rs]),
            "latency_s": mean([r["latency"] for r in rs]), "failures": sum(r["failures"] for r in rs),
            "tokens_per_solved": (sum(r["tokens"] for r in rs) / k) if k else None}
    for d in sorted({r["depth"] for r in rows}):
        sub = [r for r in rows if r["depth"] == d]
        out["by_depth"][d] = {"n": len(sub), **{s: mean([r[s]["func_hit"] for r in sub]) for s in SYSTEMS}}
    out["paired_adaptive_graph_vs_static_k3"] = {
        "both": sum(r["adaptive_graph"]["func_hit"] and r["static_k3"]["func_hit"] for r in rows),
        "only_adaptive": sum(r["adaptive_graph"]["func_hit"] and not r["static_k3"]["func_hit"] for r in rows),
        "only_static": sum(r["static_k3"]["func_hit"] and not r["adaptive_graph"]["func_hit"] for r in rows),
        "neither": sum(not r["adaptive_graph"]["func_hit"] and not r["static_k3"]["func_hit"] for r in rows)}
    return out


def print_summary(sm: dict) -> None:
    print(f"\ncases: {sm['n_cases']}   avg chunks/repo: {sm['avg_chunks']:.0f}")
    hdr = f"{'system':<15}{'func_acc':>9}{'95% CI':>15}{'grounded':>9}{'fix_ok':>8}{'tokens':>9}{'calls':>7}{'retr':>6}{'lat_s':>7}{'fail':>6}"
    print(hdr + "\n" + "-" * len(hdr))
    for s, m in sm["systems"].items():
        ci = f"[{m['func_ci95'][0]:.3f},{m['func_ci95'][1]:.3f}]"
        print(f"{s:<15}{m['func_acc']:>9.3f}{ci:>15}{m['grounded']:>9.3f}{m['fix_ok']:>8.3f}"
              f"{m['tokens']:>9.0f}{m['calls']:>7.2f}{m['retrievals']:>6.2f}{m['latency_s']:>7.2f}{m['failures']:>6}")
    print("\nfunc_acc by bug depth (hops from entry):")
    print(f"{'depth':<7}{'n':>6}" + "".join(f"{s:>16}" for s in SYSTEMS))
    for d, m in sm["by_depth"].items():
        print(f"{d:<7}{m['n']:>6}" + "".join(f"{m[s]:>16.3f}" for s in SYSTEMS))
    print("\npaired (adaptive_graph vs static_k3):", sm["paired_adaptive_graph_vs_static_k3"])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Benchmark static RAG vs adaptive context on generated bug cases")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--modules", type=int, default=40)
    ap.add_argument("--chains", type=int, default=30, help="distractor chains per repo")
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--wide-k", type=int, default=10)
    ap.add_argument("--budget", type=int, default=3000, help="token budget for adaptive prompts")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--llm", choices=["sim", "groq"], default="sim")
    ap.add_argument("--model", default=None)
    ap.add_argument("--miss", type=float, default=0.0, help="sim: probability of overlooking the real mismatch")
    ap.add_argument("--out", default="results")
    a = ap.parse_args(argv)
    if a.llm == "groq" and a.n > 50:
        print("note: real-LLM runs hit rate limits; consider --n <= 50 and --workers 1")
    gp = GenParams(a.modules, a.chains, a.max_depth)
    cfg = {"top_k": a.top_k, "wide_k": a.wide_k, "budget": a.budget, "rounds": a.rounds,
           "llm": a.llm, "model": a.model, "miss": a.miss}
    jobs = [(a.seed + i, gp, cfg) for i in range(a.n)]
    t0 = time.time()
    if a.workers <= 1:
        rows = [_work(j) for j in jobs]
    else:
        with ProcessPoolExecutor(a.workers) as ex:
            rows = list(ex.map(_work, jobs, chunksize=max(1, a.n // (a.workers * 8))))
    sm = summarize(rows)
    print_summary(sm)
    print(f"\nwall time: {time.time() - t0:.1f}s")
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "results.jsonl"), "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(a.out, "summary.json"), "w") as fh:
        json.dump({"args": vars(a), "summary": sm}, fh, indent=2)
    print(f"saved: {a.out}/results.jsonl, {a.out}/summary.json")


if __name__ == "__main__":
    main()
