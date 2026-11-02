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
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time

from ..cases import load_cases
from ..multifix import TestOracle, apply_edits, edits_from_diagnosis, failing_tests
from ..tools import IGNORE, run_pytest_in

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


def _copy(repo: str) -> tuple:
    tmp = tempfile.mkdtemp(prefix="adaptive_eval_")
    work = os.path.join(tmp, "repo")
    shutil.copytree(repo, work, ignore=IGNORE, symlinks=True)
    return tmp, work


def _suite(work: str, multi: dict, python: str, timeout: int) -> set:
    ign = " ".join(f"--ignore {p}" for p in multi.get("suite_ignore", []) if os.path.exists(os.path.join(work, p)))
    cmd = f"pytest -q -rf -p no:cacheprovider --continue-on-collection-errors {multi['suite']} {ign}"
    return failing_tests(run_pytest_in(work, None, timeout, 100000, python, cmd)["output"])


def _git_apply(work: str, patch: str, include: str | None = None) -> bool:
    cmd = ["git", "apply", *(["--include", include] if include else []), os.path.abspath(patch)]
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": os.path.dirname(work)}
    return subprocess.run(cmd, cwd=work, capture_output=True, env=env).returncode == 0


def _patch_path(bugsinpy: str, case) -> str:
    project, n = case.source["bugsinpy"].split("/")
    return os.path.join(bugsinpy, "projects", project, "bugs", n, "bug_patch.txt")


def gold_failures(case, multi: dict, python: str, bugsinpy: str, cache: dict, timeout: int) -> list:
    if case.id not in cache:
        tmp, work = _copy(case.repo)
        try:
            assert _git_apply(work, _patch_path(bugsinpy, case)), f"gold patch does not apply for {case.id}"
            cache[case.id] = sorted(_suite(work, multi, python, timeout))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return cache[case.id]


def score(case, multi: dict, diagnosis, python: str, gold: list, timeout: int) -> dict:
    edits = edits_from_diagnosis(diagnosis)
    out = {"edits": len(edits), "files_changed": [], "applied": False, "target_passed": False, "solved": False,
           "new_failures": [], "gold_files_touched": []}
    if not edits:
        return out
    tmp, work = _copy(case.repo)
    try:
        patch = apply_edits(work, edits)
        out["files_changed"] = sorted(patch.files)
        out["gold_files_touched"] = sorted(set(patch.files) & set(case.gold_files))
        if patch.problems:
            out["problems"] = patch.problems
            return out
        out["applied"] = True
        out["target_passed"] = run_pytest_in(work, None, timeout, 20, python, case.test_cmd)["passed"]
        fails = _suite(work, multi, python, timeout)
        out["new_failures"] = sorted(fails - set(gold))[:20]
        out["solved"] = out["target_passed"] and not out["new_failures"]
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_gold(cases, raw, envs, bugsinpy, timeout) -> int:
    """Recompute `needs_several_files`: does any single file of the maintainer's fix reach the full fix's result?"""
    for c in cases:
        multi, py = raw[c.id]["multi"], envs[c.env]
        cache: dict = {}
        gold = set(gold_failures(c, multi, py, bugsinpy, cache, timeout))
        single = []
        for f in c.gold_files:
            tmp, work = _copy(c.repo)
            try:
                _git_apply(work, _patch_path(bugsinpy, c), include=f)
                ok = run_pytest_in(work, None, timeout, 5, py, c.test_cmd)["passed"]
                single.append(ok and _suite(work, multi, py, timeout) <= gold)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        print(f"{c.id}: source files {len(c.gold_files)}  needs several files: {not any(single)} "
              f"(manifest says {multi['needs_several_files']})")
    return 0


def _make_llm(a):
    if a.llm == "sim":
        from ..agent_graph import SimToolLLM
        from ..sim import SimulatedLLM
        return SimToolLLM(SimulatedLLM())
    from ..llm import GroqLLM
    return GroqLLM(model=a.model, reasoning_effort=a.reasoning_effort, max_tokens=a.max_tokens)


def run_case(case, a, python: str, llm):
    from ..agent_graph import LangGraphAgent
    from ..chunking import chunk_repo, load_repo_dir
    from ..retrieval import BM25Retriever
    from ..tools import Tools
    retr = BM25Retriever(chunk_repo(load_repo_dir(case.repo)))
    tools = Tools(retr, case.repo, python=python, test_cmd=case.test_cmd)
    verifier = TestOracle(case.repo, python=python, test_cmd=case.test_cmd).check if a.mode == "agent_verify" else None
    agent = LangGraphAgent(retr, llm, tools=tools, initial_k=a.top_k, max_rounds=a.rounds, token_budget=a.budget,
                           verifier=verifier, max_verify=a.max_verify)
    return agent.run(case.bug_report)


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
    ap.add_argument("--timeout", type=int, default=900, help="seconds per test-suite run")
    ap.add_argument("--out", default=os.path.join("results", "multifile", "run"))
    a = ap.parse_args(argv)
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
    cache_path = os.path.join(os.path.dirname(a.out.rstrip("/")) or ".", "gold_failures.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    llm = _make_llm(a)
    rows = []
    for c in cases:
        multi, py = raw[c.id]["multi"], envs[c.env]
        gold = gold_failures(c, multi, py, a.bugsinpy, cache, a.timeout)
        json.dump(cache, open(cache_path, "w"), indent=1)
        t0 = time.perf_counter()
        res = run_case(c, a, py, llm)
        sc = score(c, multi, res.diagnosis, py, gold, a.timeout)
        row = {"id": c.id, "needs_several_files": multi["needs_several_files"], "mode": a.mode,
               "stop_reason": res.stop_reason, **sc, "gold_files": c.gold_files,
               "llm_calls": res.stats.llm_calls, "tool_calls": res.stats.tool_calls,
               "tokens": res.stats.total_tokens, "rounds": res.stats.rounds,
               "verify_reason": res.verification.get("reason", ""), "seconds": round(time.perf_counter() - t0, 1)}
        rows.append(row)
        with open(os.path.join(a.out, f"{c.id}.json"), "w", encoding="utf-8") as fh:
            json.dump({"row": row, "diagnosis": vars(res.diagnosis), "verification": res.verification,
                       "trace": res.trace, "context": res.context_items}, fh, indent=1, default=str)
        print(f"{c.id:15} several={str(multi['needs_several_files']):5} solved={str(sc['solved']):5} "
              f"target={str(sc['target_passed']):5} files={len(sc['files_changed'])} stop={res.stop_reason} "
              f"tokens={res.stats.total_tokens} {row['seconds']}s", flush=True)
    with open(os.path.join(a.out, "summary.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    several = [r for r in rows if r["needs_several_files"]]
    print(f"\nsolved {sum(r['solved'] for r in rows)}/{len(rows)} multi-file bugs "
          f"({sum(r['solved'] for r in several)}/{len(several)} of those that need several files); "
          f"rows in {a.out}/summary.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
