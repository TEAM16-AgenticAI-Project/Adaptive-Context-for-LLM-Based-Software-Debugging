"""adaptive-context CLI: `debug` a real repo, or `bench` on generated cases."""
from __future__ import annotations

import argparse
import json
import sys

from .adaptive import AdaptiveDebugger
from .baseline import StaticRAG
from .chunking import chunk_repo, load_repo_dir
from .llm import GroqLLM, LLMError
from .retrieval import BM25Retriever, ChromaRetriever
from .sim import SimulatedLLM
from .tools import run_failing_test, with_failure_output


def _debug(a) -> int:
    report = open(a.bug_report_file, encoding="utf-8").read() if a.bug_report_file else a.bug_report
    if not report:
        print("give --bug-report TEXT or --bug-report-file FILE", file=sys.stderr)
        return 2
    if a.run_test:
        tr = run_failing_test(a.repo, a.run_test)
        print(f"run_failing_test: passed={tr['passed']} timed_out={tr['timed_out']}", file=sys.stderr)
        report = with_failure_output(report, tr)
    files = load_repo_dir(a.repo)
    if not files:
        print(f"no .py files under {a.repo}", file=sys.stderr)
        return 2
    chunks = chunk_repo(files)
    retr = ChromaRetriever(chunks) if a.retriever == "chroma" else BM25Retriever(chunks)
    try:
        llm = SimulatedLLM() if a.llm == "sim" else GroqLLM(model=a.model)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    system = (StaticRAG(retr, llm, a.top_k) if a.mode == "baseline" else
              AdaptiveDebugger(retr, llm, initial_k=a.top_k, max_rounds=a.rounds, token_budget=a.budget,
                               prefetch_depth=a.prefetch))
    res = system.run(report)
    print(json.dumps({"diagnosis": vars(res.diagnosis), "stats": {**vars(res.stats), "total_tokens": res.stats.total_tokens},
                      "context": res.context_items, "trace": res.trace}, indent=2))
    return 0 if res.diagnosis.valid else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="adaptive-context")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("debug", help="debug a repository from a bug report")
    d.add_argument("repo")
    d.add_argument("--bug-report", default="")
    d.add_argument("--bug-report-file")
    d.add_argument("--mode", choices=["baseline", "adaptive"], default="adaptive")
    d.add_argument("--llm", choices=["groq", "sim"], default="groq")
    d.add_argument("--model", default=None)
    d.add_argument("--retriever", choices=["bm25", "chroma"], default="bm25")
    d.add_argument("--run-test", help="pytest target inside the repo; its failure output is added to the bug report")
    d.add_argument("--top-k", type=int, default=3)
    d.add_argument("--rounds", type=int, default=6)
    d.add_argument("--budget", type=int, default=3000)
    d.add_argument("--prefetch", type=int, default=2, help="callee prefetch depth (0 = off)")
    sub.add_parser("bench", help="benchmark on generated cases (see `bench --help` via python -m)")
    a, rest = ap.parse_known_args(argv)
    if a.cmd == "bench":
        from .bench.run import main as bench_main
        bench_main(rest)
        return 0
    return _debug(a)


if __name__ == "__main__":
    raise SystemExit(main())
