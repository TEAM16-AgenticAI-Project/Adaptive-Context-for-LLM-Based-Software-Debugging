"""Audits of dataset B (real BugsInPy bugs).

Two independent parts share this CLI:

* ``--audit`` (Deep): evidence audit of the real cases -> ``run_audit``.
* ``--validate-fixcheck`` (Tanay): checks that ``fixcheck.check_fix`` accepts every maintainer fix and rejects
  the unchanged buggy code -> ``validate_fixcheck``.

Fix-check validation, per real case:

1. Build the gold fix the way a model would write it: the maintainer's fixed function (plus any imports the patch
   adds) when the patch changes exactly one function, otherwise the maintainer's diff (several functions, several
   files, or module-level code). The function form is only used when applying it reproduces the maintainer's file
   exactly (same AST); otherwise the diff is used.
2. ``check_fix`` with the gold fix and the case's test command: it must apply and pass.
3. Negative control: resubmit the buggy function unchanged; it must be rejected (or, if applied, fail).
4. Baseline: the same test command on the unchanged repository must fail (the check discriminates).

The maintainer's patch is read from BugsInPy; it is evaluator-only data and is never shown to a model.

    python -m adaptive_context.bench.audit --validate-fixcheck --bugsinpy ../m2-work/BugsInPy \\
        --env py38-pytest6=../m2-work/venv38/bin/python --env py38-pytest3=../m2-work/venv38old/bin/python
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time

from ..cases import load_cases
from ..fixcheck import FixError, apply_fix_to_source, check_fix, find_function
from ..tools import run_failing_test
from .bugsinpy import git_apply

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT_MANIFEST = os.path.join(ROOT, "data", "real", "thefuck.jsonl")
_TEST_FILE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$")


# ============================================================================== --audit (Deep)
def run_audit(args) -> int:
    """Evidence audit of the real cases. Owned by Deep; implemented on his branch."""
    print("--audit is implemented in Deep's PR (bench/audit.py:run_audit)", file=sys.stderr)
    return 2


# ============================================================================== --validate-fixcheck (Tanay)
def split_patch(patch: str) -> dict:
    """{path: section text} for every non-test file in a unified git diff."""
    out, cur, buf = {}, None, []
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git"):
            if cur:
                out[cur] = "".join(buf)
            cur, buf = line.split(" b/")[-1].strip(), [line]
        elif cur:
            buf.append(line)
    if cur:
        out[cur] = "".join(buf)
    return {p: s for p, s in out.items() if not _TEST_FILE.search(p)}


def _segment(source: str, node) -> str:
    """Source of a function including its decorators, dedented."""
    lines = source.splitlines()
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return textwrap.dedent("\n".join(lines[start - 1: node.end_lineno])) + "\n"


def _same_module(a: str, b: str) -> bool:
    """Same code modulo where top-level imports sit: identical non-import statements and the same imported names.

    A model writing "imports + function" puts new imports after the last import (fixcheck's rule), while the
    maintainer may extend an existing import line or insert one mid-block; both bind the same names."""
    def norm(src):
        tree = ast.parse(src)
        names, rest = set(), []
        for n in tree.body:
            if isinstance(n, ast.Import):
                names |= {(None, al.name, al.asname, 0) for al in n.names}
            elif isinstance(n, ast.ImportFrom):
                names |= {(n.module, al.name, al.asname, n.level) for al in n.names}
            else:
                rest.append(ast.dump(n))
        return names, rest
    return norm(a) == norm(b)


def fixed_sources(repo: str, sections: dict) -> dict:
    """Apply the maintainer's patch to copies of the touched files; return {path: (buggy, fixed)}."""
    tmp = tempfile.mkdtemp(prefix="gold_fix_")
    try:
        work = os.path.join(tmp, "repo")
        for path in sections:
            os.makedirs(os.path.dirname(os.path.join(work, path)), exist_ok=True)
            shutil.copy(os.path.join(repo, path), os.path.join(work, path))
        patch_file = os.path.join(tmp, "gold.patch")
        with open(patch_file, "w", encoding="utf-8") as fh:
            fh.write("".join(sections.values()))
        git_apply(work, patch_file, reverse=False)
        out = {}
        for path in sections:
            with open(os.path.join(repo, path), encoding="utf-8") as a, \
                    open(os.path.join(work, path), encoding="utf-8") as b:
                out[path] = (a.read(), b.read())
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def gold_fix(case, patch: str) -> dict:
    """The maintainer's fix written the way a model would submit it.

    Returns {"form": "function"|"diff", "why", "fix", "file", "function", "negative"}; ``negative`` is the buggy
    function resubmitted unchanged (the negative control)."""
    sections = split_patch(patch)
    srcs = fixed_sources(case.repo, sections)
    first = case.gold_functions[0] if case.gold_functions else ""
    file, qual = (first.split("::", 1) + [""])[:2] if first else (sorted(sections)[0], "")
    negative = ""
    if qual and qual != "<module>" and file in srcs:
        try:
            _, node = find_function(ast.parse(srcs[file][0]), qual)
            negative = _segment(srcs[file][0], node)
        except FixError:
            pass
    diff = {"form": "diff", "fix": "".join(sections.values()), "file": file, "function": qual, "negative": negative}
    if len(sections) > 1:
        return {**diff, "why": "multi_file"}
    if len(case.gold_functions) != 1 or qual in ("", "<module>"):
        return {**diff, "why": "multi_function" if len(case.gold_functions) > 1 else "module_only"}
    buggy, fixed = srcs[file]
    fixed_tree = ast.parse(fixed)
    try:
        _, node = find_function(fixed_tree, qual)
    except FixError:
        return {**diff, "why": "function_renamed"}
    have = {ast.dump(n) for n in ast.parse(buggy).body if isinstance(n, (ast.Import, ast.ImportFrom))}
    new_imports = [ast.get_source_segment(fixed, n).strip() for n in fixed_tree.body
                   if isinstance(n, (ast.Import, ast.ImportFrom)) and ast.dump(n) not in have]
    candidate = "".join(i + "\n" for i in new_imports) + ("\n" if new_imports else "") + _segment(fixed, node)
    try:
        applied, _ = apply_fix_to_source(buggy, qual, candidate)
        if _same_module(applied, fixed):
            return {**diff, "form": "function", "why": "imports+function" if new_imports else "single_function",
                    "fix": candidate}
    except FixError:
        pass
    return {**diff, "why": "module_level"}


def _revision() -> dict:
    def git(*a):
        p = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""
    dirty = git("status", "--porcelain", "--", "src")
    return {"commit": git("rev-parse", "HEAD"), "src_dirty": bool(dirty)}


def _brief(r: dict) -> dict:
    return {k: r[k] for k in ("applied", "mode", "passed", "reason")}


def validate_case(case, patch: str, python: str | None, timeout: int = 120) -> dict:
    g = gold_fix(case, patch)
    t0 = time.perf_counter()
    gold = check_fix(case.repo, g["file"], g["function"], g["fix"], test_cmd=case.test_cmd, python=python,
                     timeout=timeout)
    gold_s = round(time.perf_counter() - t0, 2)
    if g["negative"]:
        neg = check_fix(case.repo, g["file"], g["function"], g["negative"], test_cmd=case.test_cmd, python=python,
                        timeout=timeout)
    else:
        neg = {"applied": False, "mode": "", "passed": False, "reason": "no_negative", "output": ""}
    base = run_failing_test(case.repo, None, timeout, 40, python, case.test_cmd)
    rejected = not neg["passed"] and neg["reason"] != "no_negative"
    rec = {"id": case.id, "env": case.env, "files": len(case.gold_files), "functions": len(case.gold_functions),
           "module_level_change": case.module_level_change, "form": g["form"], "why": g["why"],
           "target": f"{g['file']}::{g['function']}", "gold": {**_brief(gold), "secs": gold_s},
           "negative": _brief(neg), "baseline_failed": not base["passed"],
           "ok": bool(gold["passed"] and rejected and not base["passed"])}
    if not rec["ok"]:
        rec["gold_output"] = gold["output"][-2000:]
        rec["negative_output"] = neg["output"][-2000:]
    return rec


def summarize(records: list, revision: dict) -> dict:
    n = len(records)

    def count(pred):
        return sum(1 for r in records if pred(r))
    forms = {}
    for r in records:
        f = forms.setdefault(r["form"], {"cases": 0, "gold_passed": 0})
        f["cases"] += 1
        f["gold_passed"] += int(r["gold"]["passed"])
    return {"revision": revision, "cases": n,
            "gold_passed": count(lambda r: r["gold"]["passed"]),
            "negative_rejected": count(lambda r: not r["negative"]["passed"] and r["negative"]["reason"] != "no_negative"),
            "negative_reasons": sorted({r["negative"]["reason"] for r in records}),
            "baseline_failed": count(lambda r: r["baseline_failed"]),
            "all_ok": count(lambda r: r["ok"]),
            "by_form": forms,
            "by_why": {w: count(lambda r, w=w: r["why"] == w) for w in sorted({r["why"] for r in records})},
            "not_ok": [r["id"] for r in records if not r["ok"]]}


def markdown(records: list, summary: dict) -> str:
    rev = summary["revision"]
    lines = ["# Fix-check validation on dataset B (BugsInPy/thefuck, real tests, no LLM)", "",
             f"Commit `{rev['commit'][:10]}`{' (src dirty)' if rev['src_dirty'] else ''}. "
             f"Gold fix passes: **{summary['gold_passed']}/{summary['cases']}**; "
             f"negative control rejected: **{summary['negative_rejected']}/{summary['cases']}**; "
             f"unchanged repo fails: **{summary['baseline_failed']}/{summary['cases']}**.", "",
             "| Case | Gold form | Why | Gold check | Negative control | Unchanged repo |",
             "|---|---|---|---|---|---|"]
    for r in records:
        g, ng = r["gold"], r["negative"]
        lines.append(f"| {r['id']} | {r['form']} | {r['why']} | {g['reason']} ({g['mode'] or '-'}) | "
                     f"{'rejected' if not ng['passed'] else 'ACCEPTED'}: {ng['reason']} | "
                     f"{'fails' if r['baseline_failed'] else 'PASSES'} |")
    return "\n".join(lines) + "\n"


def validate_fixcheck(manifest: str, bugsinpy: str, envs: dict, out_dir: str, timeout: int = 120,
                      only: set | None = None, project: str = "thefuck") -> dict:
    cases = [c for c in load_cases(manifest) if not only or c.id in only]
    records = []
    for case in cases:
        bug = case.source.get("bugsinpy", "").split("/")[-1]
        with open(os.path.join(bugsinpy, "projects", project, "bugs", bug, "bug_patch.txt"), encoding="utf-8") as fh:
            patch = fh.read()
        rec = validate_case(case, patch, envs.get(case.env), timeout)
        records.append(rec)
        print(f"{rec['id']:>11}  {rec['form']:<8} gold={rec['gold']['reason']:<10} neg={rec['negative']['reason']:<12} "
              f"baseline_failed={rec['baseline_failed']}  {'OK' if rec['ok'] else 'CHECK'}", flush=True)
    summary = summarize(records, _revision())
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "fixcheck_validation.jsonl"), "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    with open(os.path.join(out_dir, "fixcheck_validation.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    with open(os.path.join(out_dir, "fixcheck_validation.md"), "w", encoding="utf-8") as fh:
        fh.write(markdown(records, summary))
    return summary


# ============================================================================== CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audits of the real-bug dataset")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audit", action="store_true", help="evidence audit (Deep)")
    mode.add_argument("--validate-fixcheck", action="store_true", help="fix check on every maintainer fix (Tanay)")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--bugsinpy", help="BugsInPy checkout (for the maintainer patches)")
    ap.add_argument("--project", default="thefuck")
    ap.add_argument("--env", action="append", default=[], help="NAME=PYTHON (repeatable)")
    ap.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    ap.add_argument("--out", default=os.path.join(ROOT, "results"))
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args(argv)
    if a.audit:
        return run_audit(a)
    if not a.bugsinpy:
        ap.error("--validate-fixcheck needs --bugsinpy")
    envs = dict(e.split("=", 1) for e in a.env)
    only = {c.strip() for c in a.cases.split(",") if c.strip()} or None
    s = validate_fixcheck(a.manifest, a.bugsinpy, envs, a.out, a.timeout, only, a.project)
    print(json.dumps({k: v for k, v in s.items() if k != "revision"}, indent=2))
    return 0 if s["all_ok"] == s["cases"] else 1


if __name__ == "__main__":                                    # pragma: no cover
    sys.exit(main())
