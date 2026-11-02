"""adaptive-context CLI.

  debug   debug one repository from a bug report (baseline | adaptive | agent | agent_verify)
  bench   synthetic benchmark on generated cases            (python -m adaptive_context.bench.run --help)
  cases   compare systems on a real/controlled case manifest (python -m adaptive_context.bench.run_cases --help)
  audit   LLM-free evidence audit + fix-check validation     (python -m adaptive_context.bench.audit --help)
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import warnings

from .adaptive import AdaptiveDebugger
from .baseline import StaticRAG
from .chunking import chunk_repo, load_repo_dir
from .llm import GroqLLM, LLMError
from .retrieval import BM25Retriever, ChromaRetriever
from .sim import SimulatedLLM
from .tools import Tools, run_failing_test, with_failure_output


def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return n


def _debug(a) -> int:
    report = open(a.bug_report_file, encoding="utf-8").read() if a.bug_report_file else a.bug_report
    test_cmd = None
    if a.case:                                  # MANIFEST:ID -> repo, bug report, test command, test Python
        from .cases import load_cases
        manifest, cid = a.case.rsplit(":", 1)
        case = next((c for c in load_cases(manifest) if c.id == cid), None)
        if case is None:
            print(f"no case {cid!r} in {manifest}", file=sys.stderr)
            return 2
        a.repo, report, test_cmd = case.repo, report or case.bug_report, case.test_cmd or None
        a.python = a.python or dict(e.split("=", 1) for e in a.env).get(case.env)
    if not a.repo:
        print("give a repository path or --case MANIFEST:ID", file=sys.stderr)
        return 2
    if not report:
        print("give --bug-report TEXT or --bug-report-file FILE", file=sys.stderr)
        return 2
    if a.run_test and a.mode in ("baseline", "adaptive"):
        tr = run_failing_test(a.repo, a.run_test, python=a.python)
        print(f"run_failing_test: passed={tr['passed']} timed_out={tr['timed_out']}", file=sys.stderr)
        report = with_failure_output(report, tr)
    files = load_repo_dir(a.repo)
    if not files:
        print(f"no .py files under {a.repo}", file=sys.stderr)
        return 2
    chunks = chunk_repo(files)
    retr = ChromaRetriever(chunks) if a.retriever == "chroma" else BM25Retriever(chunks)
    try:
        llm = SimulatedLLM() if a.llm == "sim" else GroqLLM(model=a.model, reasoning_effort=a.reasoning_effort)
    except LLMError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.mode == "baseline":
        system = StaticRAG(retr, llm, a.top_k)
    elif a.mode == "adaptive":
        system = AdaptiveDebugger(retr, llm, initial_k=a.top_k, max_rounds=a.rounds, token_budget=a.budget,
                                  prefetch_depth=a.prefetch)
    else:
        from .agent_graph import LangGraphAgent, SimToolLLM
        if a.llm == "sim":
            llm = SimToolLLM(llm)
        tools = Tools(retr, a.repo, test_path=a.run_test, python=a.python, test_cmd=test_cmd)
        verifier = None
        if a.mode == "agent_verify":
            try:
                from .fixcheck import check_fix          # verification lives in Tanay's PR
            except ImportError:
                print("--mode agent_verify needs fixcheck.py (verification PR)", file=sys.stderr)
                return 2
            if not (a.run_test or test_cmd):
                print("--mode agent_verify needs --run-test or --case (the regression test to rerun)", file=sys.stderr)
                return 2
            verifier = lambda d: check_fix(a.repo, d.file, d.function, d.fix, test_path=a.run_test,  # noqa: E731
                                           test_cmd=test_cmd, python=a.python)
        system = LangGraphAgent(retr, llm, tools=tools, initial_k=a.top_k, max_rounds=a.rounds,
                                token_budget=a.budget, verifier=verifier)
    res = system.run(report)
    print(json.dumps({"diagnosis": vars(res.diagnosis), "stop_reason": res.stop_reason,
                      "stats": {**vars(res.stats), "total_tokens": res.stats.total_tokens},
                      "verification": res.verification, "context": res.context_items, "trace": res.trace},
                     indent=2, default=str))
    return 0 if res.diagnosis.valid else 1


def main(argv=None) -> int:
    # old repos (e.g. thefuck) contain regexes like '\(' that newer Pythons warn about while parsing; not our code
    warnings.filterwarnings("ignore", category=SyntaxWarning)
    ap = argparse.ArgumentParser(prog="adaptive-context")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("debug", help="debug a repository from a bug report")
    d.add_argument("repo", nargs="?", default="")
    d.add_argument("--case", help="MANIFEST:ID, e.g. data/real/thefuck.jsonl:thefuck-5 (repo, report and test from the case)")
    d.add_argument("--env", action="append", default=[], help="NAME=PYTHON for --case test environments")
    d.add_argument("--bug-report", default="")
    d.add_argument("--bug-report-file")
    d.add_argument("--mode", choices=["baseline", "adaptive", "agent", "agent_verify"], default="adaptive")
    d.add_argument("--llm", choices=["groq", "sim"], default="groq")
    d.add_argument("--model", default=None)
    d.add_argument("--reasoning-effort", default=None, choices=["low", "medium", "high"],
                   help="gpt-oss reasoning effort (default: $GROQ_REASONING_EFFORT or low)")
    d.add_argument("--retriever", choices=["bm25", "chroma"], default="bm25")
    d.add_argument("--run-test", help="pytest target inside the repo (agent: enables run_failing_test; "
                                      "baseline/adaptive: its output is added to the bug report)")
    d.add_argument("--python", default=None, help="Python used to run the repo's tests (default: this one)")
    d.add_argument("--top-k", type=_positive, default=3)
    d.add_argument("--rounds", type=_positive, default=6)
    d.add_argument("--budget", type=_positive, default=3000)
    d.add_argument("--prefetch", type=int, default=2, help="adaptive mode: callee prefetch depth (0 = off)")
    for name, text in (("bench", "benchmark on generated cases"), ("cases", "compare systems on a case manifest"),
                       ("audit", "LLM-free evidence audit and fix-check validation")):
        sub.add_parser(name, help=text, add_help=False)          # --help goes to the runner itself
    a, rest = ap.parse_known_args(argv)
    if a.cmd != "debug":
        module = {"bench": "run", "cases": "run_cases", "audit": "audit"}[a.cmd]
        try:                                        # run_cases (evaluation PR) and audit (datasets PR) land later
            runner = importlib.import_module(f".bench.{module}", __package__).main
        except ModuleNotFoundError:
            print(f"`{a.cmd}` needs bench/{module}.py, which is not on this branch yet", file=sys.stderr)
            return 2
        runner(rest)
        return 0
    if rest:
        ap.error(f"unrecognized arguments: {' '.join(rest)}")
    return _debug(a)


if __name__ == "__main__":
    raise SystemExit(main())
