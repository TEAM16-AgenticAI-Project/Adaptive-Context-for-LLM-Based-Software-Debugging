"""adaptive-context CLI.

  debug   debug one repository from a bug report (baseline | adaptive | agent | agent_verify)
  bench   synthetic benchmark on generated cases            (python -m adaptive_context.bench.run --help)
  cases   compare systems on a real/controlled case manifest (python -m adaptive_context.bench.run_cases --help)
  audit   LLM-free evidence audit + fix-check validation     (python -m adaptive_context.bench.audit --help)
  ui      web UI (pip install -e ".[ui]")                    (python -m adaptive_context.webui.server --help)
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import warnings

from .bench.systems import make_system
from .chunking import chunk_repo, load_repo_dir
from .llm import LLMError, make_llm
from .retrieval import BM25Retriever, ChromaRetriever
from .tools import Tools, run_failing_test, with_failure_output


MODES = ("baseline", "adaptive", "agent", "agent_verify")


def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return n


def build_debugger(repo: str, report: str, *, mode: str = "adaptive", llm: str = "groq", python: str | None = None,
                   test_cmd: str | None = None, run_test: str | None = None, model: str | None = None,
                   reasoning_effort: str | None = None, max_tokens: int = 2048, retriever: str = "bm25", top_k: int = 3,
                   rounds: int = 6, budget: int = 3000, prefetch: int = 2, max_verify: int = 3,
                   time_limit: int | None = None):
    """Build the debugger for one repository; shared by `adaptive-context debug` and the web UI.

    mode is baseline | adaptive | agent | agent_verify. The test (run_test: a path, or test_cmd: a command) is the
    failing regression test: baseline/adaptive get its output added to the report, the agent can rerun it, and
    agent_verify checks every proposed fix (all of its places, in any file) with it and the related tests.
    Returns (system, report, info). Raises ValueError for bad input, LLMError without an API key and RuntimeError
    for the agent modes without langgraph."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    if run_test and mode in ("baseline", "adaptive"):
        tr = run_failing_test(repo, run_test, python=python)
        print(f"run_failing_test: passed={tr['passed']} timed_out={tr['timed_out']}", file=sys.stderr)
        report = with_failure_output(report, tr)
    files = load_repo_dir(repo)
    if not files:
        raise ValueError(f"no .py files under {repo}")
    chunks = chunk_repo(files)
    retr = ChromaRetriever(chunks) if retriever == "chroma" else BM25Retriever(chunks)
    agentic = mode in ("agent", "agent_verify")
    llm_obj = make_llm(llm, model=model, reasoning_effort=reasoning_effort, max_tokens=max_tokens, tools=agentic)
    cfg = {"top_k": top_k, "rounds": rounds, "budget": budget, "prefetch_depth": prefetch,
           "max_verify": max_verify, "time_limit": time_limit}
    name = {"baseline": "static_k3", "adaptive": "adaptive_graph" if prefetch else "adaptive"}.get(mode, mode)
    tools = verifier = None
    if agentic:
        tools = Tools(retr, repo, test_path=run_test, python=python, test_cmd=test_cmd)
    if mode == "agent_verify":
        if not (run_test or test_cmd):
            raise ValueError("agent_verify needs the failing test (--run-test, or a case / test command)")
        # multi-location check (fix + other_changes, any file) with related tests; see multifix.py
        from .multifix import TestOracle
        verifier = TestOracle(repo, python=python, test_cmd=test_cmd, test_path=run_test).check
    system = make_system(name, retr, llm_obj, cfg, tools=tools, verifier=verifier)
    return system, report, {"files": len(files), "chunks": len(chunks), "system": name}


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
    try:
        system, report, _ = build_debugger(a.repo, report, mode=a.mode, llm=a.llm, python=a.python, test_cmd=test_cmd,
                                           run_test=a.run_test, model=a.model, reasoning_effort=a.reasoning_effort,
                                           max_tokens=a.max_tokens, retriever=a.retriever, top_k=a.top_k,
                                           rounds=a.rounds, budget=a.budget, prefetch=a.prefetch,
                                           max_verify=a.max_verify, time_limit=a.time_limit or None)
    except (ValueError, LLMError, RuntimeError) as e:   # bad input, no API key, or agent modes without langgraph
        print(f"error: {e}", file=sys.stderr)
        return 2
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
    d.add_argument("--mode", choices=MODES, default="adaptive")
    d.add_argument("--llm", choices=["groq", "sim"], default="groq")
    d.add_argument("--model", default=None)
    d.add_argument("--max-tokens", type=_positive, default=2048, help="LLM output tokens (use 4096 for multi-file)")
    d.add_argument("--reasoning-effort", default=None, choices=["low", "medium", "high"],
                   help="gpt-oss reasoning effort (default: $GROQ_REASONING_EFFORT or low)")
    d.add_argument("--retriever", choices=["bm25", "chroma"], default="bm25")
    d.add_argument("--run-test", help="pytest target inside the repo (agent: enables run_failing_test; "
                                      "baseline/adaptive: its output is added to the bug report)")
    d.add_argument("--python", default=None, help="Python used to run the repo's tests (default: this one)")
    d.add_argument("--top-k", type=_positive, default=3)
    d.add_argument("--rounds", type=_positive, default=6)
    d.add_argument("--time-limit", type=int, default=0, help="agent modes: stop after this many seconds (0 = none)")
    d.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"],
                   help="progress log on stderr (stdout keeps the JSON result)")
    d.add_argument("--log-file", default=None, help="also write the log to this file")
    d.add_argument("--max-verify", type=_positive, default=3,
                   help="agent_verify: how many failed fix checks the agent may repair (each grants 2 rounds)")
    d.add_argument("--budget", type=_positive, default=3000)
    d.add_argument("--prefetch", type=int, default=2, help="adaptive mode: callee prefetch depth (0 = off)")
    for name, text in (("bench", "benchmark on generated cases"), ("cases", "compare systems on a case manifest"),
                       ("audit", "LLM-free evidence audit and fix-check validation"),
                       ("ui", "web UI: run a system on a bug and see every file the fix changes")):
        sub.add_parser(name, help=text, add_help=False)          # --help goes to the runner itself
    a, rest = ap.parse_known_args(argv)
    if a.cmd != "debug":
        module = {"bench": "bench.run", "cases": "bench.run_cases", "audit": "bench.audit", "ui": "webui.server"}[a.cmd]
        try:                                        # run_cases (evaluation PR) and audit (datasets PR) land later
            runner = importlib.import_module(f".{module}", __package__).main
        except ModuleNotFoundError:
            print(f"`{a.cmd}` needs {module.replace('.', '/')}.py, which is not on this branch yet", file=sys.stderr)
            return 2
        runner(rest)
        return 0
    if rest:
        ap.error(f"unrecognized arguments: {' '.join(rest)}")
    from .logs import setup_logging
    setup_logging(a.log_level, a.log_file)
    return _debug(a)


if __name__ == "__main__":
    raise SystemExit(main())
