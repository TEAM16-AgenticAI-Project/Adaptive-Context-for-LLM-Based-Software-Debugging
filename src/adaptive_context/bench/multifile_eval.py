"""Multi-file bug evaluation: run the agent on real bugs whose maintainer fix spans several source files and
score the result with the projects' own test suites.

A case is SOLVED when, after applying every change the agent proposed (fix + other_changes):
  1. the regression test passes, and
  2. no test in the offline suite fails that the maintainer's fix passes (the agent fix is at least as good
     as the maintainer's on the whole suite).
Also reported: how many files the agent changed, and which of the maintainer's files it touched.

`needs_several_files` (evaluator-only, in the manifest) marks bugs where no single file of the maintainer's fix
reaches the full fix's test result; see `--check-gold`, which recomputes it.

Examples:
  python -m adaptive_context.bench.multifile_eval --make-manifest                     # data/real/multifile.jsonl
  python -m adaptive_context.bench.multifile_eval --check-gold --bugsinpy ../m2-work/BugsInPy --env ...
  python -m adaptive_context.bench.multifile_eval --mode agent_verify --bugsinpy ../m2-work/BugsInPy \\
      --env py38-pytest3=... --env py38-pytest6=... --env py38=... --env py38-tornado=... --env py38-tqdm=... \\
      --out results/multifile/agent_verify
Live runs need GROQ_API_KEY; `--llm sim` runs the pipeline offline (the simulator cannot solve real bugs).

Robustness: every step is logged to the terminal and to <out>/run.log (`tail -f` it); each bug runs in its own
worker process with a hard wall-clock limit (--case-timeout); the agent stops itself before that (--agent-time),
every test run is bounded (--timeout) and killed with its subprocesses; results are saved after every bug.
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import signal
import time
import traceback

from ..cases import load_cases
from ..llm import make_llm
from ..logs import setup_logging, short
from ..multifix import TestOracle, apply_edits, edits_from_diagnosis, failing_tests
from ..tools import kill_active_tests, run_pytest_in, temp_repo
from .bugsinpy import git_apply

log = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "..", "..", "data", "real"))
MANIFEST = os.path.join(DATA, "multifile.jsonl")

# network-dependent youtube-dl tests (downloads, live sites): excluded from the offline suite
_YTDL_NET = ["test/test_download.py", "test/test_youtube_signature.py", "test/test_subtitles.py",
             "test/test_age_restriction.py", "test/test_iqiyi_sdk_interpreter.py", "test/test_write_annotations.py",
             "test/test_youtube_lists.py", "test/test_socks.py", "test/test_write_info_json.py",
             "test/test_youtube_subtitles.py", "test/test_playlists.py"]
# id -> (offline suite, ignored paths, needs several files). Every case's maintainer fix touches >= 2 source files.
SPEC = {
    "thefuck-16": ("tests", ["tests/functional"], True),
    "thefuck-17": ("tests", ["tests/functional"], False),
    **{f"youtube-dl-{n}": ("test", _YTDL_NET, n == 42) for n in (16, 31, 32, 33, 35, 38, 39, 40, 42)},
    "tornado-6": ("tornado/test", [], False),
    "tornado-10": ("tornado/test", [], True),
    "tqdm-2": ("tqdm/tests/tests_tqdm.py", [], True),
}


def make_manifest(out: str = MANIFEST) -> int:
    rows = {}
    for name in ("thefuck", "youtube-dl", "tornado", "tqdm"):
        path = os.path.join(DATA, f"{name}.jsonl")
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                r = json.loads(line)
                rows[r["id"]] = r
    with open(out, "w", encoding="utf-8") as fh:
        for cid, (suite, ignore, several) in SPEC.items():
            if cid not in rows:
                print(f"missing {cid}: build it with bench.bugsinpy first")
                continue
            r = dict(rows[cid])
            r["multi"] = {"suite": suite, "suite_ignore": ignore, "source_files": len(r["gold"]["files"]),
                          "needs_several_files": several}
            fh.write(json.dumps(r) + "\n")
    print(f"saved {out}")
    return 0


def _suite(work: str, multi: dict, python: str, timeout: int) -> set:
    ign = " ".join(f"--ignore {p}" for p in multi.get("suite_ignore", []) if os.path.exists(os.path.join(work, p)))
    cmd = f"pytest -q -rf -p no:cacheprovider --continue-on-collection-errors {multi['suite']} {ign}"
    return failing_tests(run_pytest_in(work, None, timeout, 100000, python, cmd)["output"])


def _patch_path(bugsinpy: str, case) -> str:
    project, n = case.source["bugsinpy"].split("/")
    return os.path.join(bugsinpy, "projects", project, "bugs", n, "bug_patch.txt")


def gold_failures(case, multi: dict, python: str, bugsinpy: str, cache: dict, timeout: int) -> list:
    if case.id not in cache:
        t0 = time.perf_counter()
        log.info("%s: running the offline suite with the maintainer's fix (reference, cached)...", case.id)
        with temp_repo(case.repo, "adaptive_eval_") as work:
            git_apply(work, _patch_path(bugsinpy, case))                  # raises if the gold patch fails
            cache[case.id] = sorted(_suite(work, multi, python, timeout))
        log.info("%s: reference done in %.0fs (%d tests fail even with the maintainer's fix)", case.id,
                 time.perf_counter() - t0, len(cache[case.id]))
    return cache[case.id]


def score(case, multi: dict, diagnosis, python: str, gold: list, timeout: int) -> dict:
    edits = edits_from_diagnosis(diagnosis)
    out = {"edits": len(edits), "files_changed": [], "applied": False, "target_passed": False, "solved": False,
           "new_failures": [], "gold_files_touched": []}
    if not edits:
        return out
    with temp_repo(case.repo, "adaptive_eval_") as work:
        patch = apply_edits(work, edits)
        out["files_changed"] = sorted(patch.files)
        out["gold_files_touched"] = sorted(set(patch.files) & set(case.gold_files))
        if patch.problems:
            out["problems"] = patch.problems
            return out
        out["applied"] = True
        out["target_passed"] = run_pytest_in(work, None, timeout, 20, python, case.test_cmd)["passed"]
        t0 = time.perf_counter()
        fails = _suite(work, multi, python, timeout)
        log.info("%s: scored on the offline suite in %.0fs", case.id, time.perf_counter() - t0)
        out["new_failures"] = sorted(fails - set(gold))[:20]
        out["solved"] = out["target_passed"] and not out["new_failures"]
        return out


def check_gold(cases, raw, envs, bugsinpy, timeout) -> int:
    """Recompute `needs_several_files`: does any single file of the maintainer's fix reach the full fix's result?"""
    for c in cases:
        multi, py = raw[c.id]["multi"], envs[c.env]
        cache: dict = {}
        gold = set(gold_failures(c, multi, py, bugsinpy, cache, timeout))
        single = []
        for f in c.gold_files:
            with temp_repo(c.repo, "adaptive_eval_") as work:
                git_apply(work, _patch_path(bugsinpy, c), include=f, strict=False)
                ok = run_pytest_in(work, None, timeout, 5, py, c.test_cmd)["passed"]
                single.append(ok and _suite(work, multi, py, timeout) <= gold)
        print(f"{c.id}: source files {len(c.gold_files)}  needs several files: {not any(single)} "
              f"(manifest says {multi['needs_several_files']})")
    return 0


def _make_llm(a):
    return make_llm(a.llm, model=a.model, reasoning_effort=a.reasoning_effort, max_tokens=a.max_tokens, tools=True)


def run_case(case, a, python: str, llm):
    from ..chunking import chunk_repo, load_repo_dir
    from ..retrieval import BM25Retriever
    from ..tools import Tools
    from .systems import make_system
    retr = BM25Retriever(chunk_repo(load_repo_dir(case.repo)))
    tools = Tools(retr, case.repo, python=python, test_cmd=case.test_cmd)
    verifier = TestOracle(case.repo, python=python, test_cmd=case.test_cmd).check if a.mode == "agent_verify" else None
    agent = make_system(a.mode, retr, llm, {"top_k": a.top_k, "rounds": a.rounds, "budget": a.budget,
                                            "max_verify": a.max_verify, "time_limit": a.agent_time},
                        tools=tools, verifier=verifier)
    return agent.run(case.bug_report)


def _case_worker(cid: str, opts: dict, python: str, gold: list, multi: dict, log_file: str) -> None:
    """One bug, in its own process: agent run + scoring; writes <out>/<id>.json. Killed on --case-timeout."""
    setup_logging(opts["log_level"], log_file)

    def _stop(*_):                                   # parent ran out of patience: take the test runs down too
        kill_active_tests()
        os._exit(124)
    signal.signal(signal.SIGTERM, _stop)
    a = argparse.Namespace(**opts)
    case = next(c for c in load_cases(a.manifest) if c.id == cid)
    t0 = time.perf_counter()
    res = run_case(case, a, python, _make_llm(a))
    log.info("%s: agent finished (%s); scoring...", cid, res.stop_reason)
    sc = score(case, multi, res.diagnosis, python, gold, a.timeout)
    row = {"id": cid, "needs_several_files": multi["needs_several_files"], "mode": a.mode,
           "stop_reason": res.stop_reason, **sc, "gold_files": case.gold_files,
           "llm_calls": res.stats.llm_calls, "tool_calls": res.stats.tool_calls,
           "tokens": res.stats.total_tokens, "rounds": res.stats.rounds,
           "verify_reason": res.verification.get("reason", ""), "seconds": round(time.perf_counter() - t0, 1)}
    with open(os.path.join(a.out, f"{cid}.json"), "w", encoding="utf-8") as fh:
        json.dump({"row": row, "diagnosis": vars(res.diagnosis), "verification": res.verification,
                   "trace": res.trace, "context": res.context_items}, fh, indent=1, default=str)


def _run_isolated(c, a, python: str, gold: list, multi: dict, log_file: str) -> dict:
    """Run one bug in a worker process with a hard wall-clock limit and a heartbeat in the log."""
    path = os.path.join(a.out, f"{c.id}.json")
    if os.path.exists(path):
        os.remove(path)
    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(target=_case_worker, args=(c.id, vars(a), python, gold, multi, log_file), name=c.id)
    t0 = time.perf_counter()
    p.start()
    while p.is_alive() and time.perf_counter() - t0 < a.case_timeout:
        p.join(min(60, max(1, a.case_timeout - (time.perf_counter() - t0))))
        if p.is_alive():
            log.info("%s: still running (%.0fs of %ds)", c.id, time.perf_counter() - t0, a.case_timeout)
    base = {"id": c.id, "needs_several_files": multi["needs_several_files"], "mode": a.mode, "solved": False,
            "target_passed": False, "files_changed": [], "gold_files": c.gold_files,
            "seconds": round(time.perf_counter() - t0, 1)}
    if p.is_alive():
        log.error("%s: hit the %ds case limit: stopping it and its test runs", c.id, a.case_timeout)
        p.terminate()
        p.join(20)
        if p.is_alive():
            p.kill()
            p.join(5)
        return {**base, "stop_reason": "case_timeout"}
    if not os.path.exists(path):
        log.error("%s: worker exited with code %s without a result (see the log above)", c.id, p.exitcode)
        return {**base, "stop_reason": f"crashed (exit {p.exitcode})"}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["row"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--make-manifest", action="store_true", help="assemble data/real/multifile.jsonl and exit")
    ap.add_argument("--check-gold", action="store_true", help="recompute needs_several_files and exit")
    ap.add_argument("--bugsinpy", default=os.path.join(DATA, "..", "..", "..", "m2-work", "BugsInPy"))
    ap.add_argument("--env", action="append", default=[], help="NAME=PYTHON for each case env (repeatable)")
    ap.add_argument("--cases", default="", help="comma-separated ids (default: all)")
    ap.add_argument("--several-only", action="store_true", help="only bugs that need several files")
    ap.add_argument("--mode", choices=["agent", "agent_verify"], default="agent_verify")
    ap.add_argument("--llm", choices=["groq", "sim"], default="groq")
    ap.add_argument("--model", default=None)
    ap.add_argument("--reasoning-effort", default=None, choices=["low", "medium", "high"])
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--budget", type=int, default=4000, help="token budget per prompt (multi-file needs more)")
    ap.add_argument("--max-tokens", type=int, default=4096, help="LLM output tokens (fixes in several places)")
    ap.add_argument("--max-verify", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=600, help="seconds per test-suite run (killed after)")
    ap.add_argument("--case-timeout", type=int, default=1800, help="hard limit per bug, agent + scoring (seconds)")
    ap.add_argument("--agent-time", type=int, default=0,
                    help="the agent stops itself after this many seconds (default: case-timeout - timeout - 60)")
    ap.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    ap.add_argument("--out", default=os.path.join("results", "multifile", "run"))
    a = ap.parse_args(argv)
    a.agent_time = a.agent_time or max(300, a.case_timeout - a.timeout - 60)
    if a.make_manifest:
        return make_manifest(a.manifest)
    raw = {json.loads(line)["id"]: json.loads(line) for line in open(a.manifest, encoding="utf-8")}
    cases = load_cases(a.manifest)
    if a.cases:
        keep = set(a.cases.split(","))
        cases = [c for c in cases if c.id in keep]
    if a.several_only:
        cases = [c for c in cases if raw[c.id]["multi"]["needs_several_files"]]
    envs = dict(e.split("=", 1) for e in a.env)
    missing = sorted({c.env for c in cases} - set(envs))
    if missing:
        ap.error(f"give --env for: {', '.join(missing)}")
    if a.check_gold:
        return check_gold(cases, raw, envs, a.bugsinpy, a.timeout)
    os.makedirs(a.out, exist_ok=True)
    a.out, a.manifest = os.path.abspath(a.out), os.path.abspath(a.manifest)
    log_file = os.path.join(a.out, "run.log")
    setup_logging(a.log_level, log_file)
    log.info("multi-file evaluation: %d bug(s), mode %s, model %s | limits: %ds per bug, agent %ds, %ds per test "
             "run | log: %s", len(cases), a.mode, a.model or "default", a.case_timeout, a.agent_time, a.timeout,
             log_file)
    cache_path = os.path.join(os.path.dirname(a.out.rstrip("/")) or ".", "gold_failures.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    if a.llm == "groq":
        _make_llm(a)                                     # fail fast on a missing API key, before any test runs
    rows = []
    for i, c in enumerate(cases, 1):
        multi, py = raw[c.id]["multi"], envs[c.env]
        log.info("===== [%d/%d] %s (env %s, needs several files: %s)", i, len(cases), c.id, c.env,
                 multi["needs_several_files"])
        try:
            gold = gold_failures(c, multi, py, a.bugsinpy, cache, a.timeout)
            with open(cache_path, "w") as fh:
                json.dump(cache, fh, indent=1)
            row = _run_isolated(c, a, py, gold, multi, log_file)
        except Exception as e:                           # one broken case must not stop the run
            log.error("%s: failed: %s\n%s", c.id, short(e, 300), traceback.format_exc())
            row = {"id": c.id, "needs_several_files": multi["needs_several_files"], "mode": a.mode,
                   "stop_reason": f"error: {short(e, 120)}", "solved": False, "target_passed": False,
                   "files_changed": [], "gold_files": c.gold_files}
        rows.append(row)
        with open(os.path.join(a.out, "summary.jsonl"), "w", encoding="utf-8") as fh:      # saved after every bug
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        log.info("===== %s: solved=%s target=%s files=%d stop=%s tokens=%s %ss", c.id, row.get("solved"),
                 row.get("target_passed"), len(row.get("files_changed", [])), row.get("stop_reason"),
                 row.get("tokens", "-"), row.get("seconds", "-"))
    several = [r for r in rows if r["needs_several_files"]]
    log.info("solved %d/%d multi-file bugs (%d/%d of those that need several files); rows in %s/summary.jsonl",
             sum(bool(r.get("solved")) for r in rows), len(rows), sum(bool(r.get("solved")) for r in several),
             len(several), a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
