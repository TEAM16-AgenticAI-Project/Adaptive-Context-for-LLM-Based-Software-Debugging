"""Build a real-bug case manifest from BugsInPy (https://github.com/soarsmu/BugsInPy).

For each bug it follows the BugsInPy checkout procedure:
  1. export the FIXED commit of the project (its tests include the regression test)
  2. reverse-apply bug_patch.txt to get the buggy source
  3. run the failing test with the configured Python: it must FAIL on the buggy source (reproduced)
  4. re-apply the patch and run again: it must PASS (gold fix verified), then reverse it again
The bug report shown to the model is the failing test id plus the tail of its pytest output.
Gold files/functions come from the patch hunks mapped onto the buggy source; they are stored only in the
evaluator-only `gold` field.

Example:
  python -m adaptive_context.bench.bugsinpy --bugsinpy ../BugsInPy --project thefuck \
      --clone ../thefuck --out data/real --bugs 1-32 \
      --env py38-pytest6=/path/venv38/bin/python --env py38-pytest3=/path/venv38old/bin/python \
      --env-for 3-21=py38-pytest3 --default-env py38-pytest6
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile

from ..tools import run_failing_test

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _ranges(spec: str) -> list:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def read_info(path: str) -> dict:
    info = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "=" in line:
                k, v = line.split("=", 1)
                info[k.strip()] = v.strip().strip('"')
    return info


def patch_changed_lines(patch: str) -> dict:
    """{file: set(old-side line numbers touched)} for non-test files. Old side = buggy source."""
    out, cur, old_no = {}, None, 0
    for line in patch.splitlines():
        if line.startswith("diff --git"):
            cur = line.split(" b/")[-1].strip()
            continue
        m = _HUNK.match(line)
        if m and cur:
            old_no = int(m.group(1))
            continue
        if cur is None or line.startswith(("---", "+++", "index ")):
            continue
        if line.startswith("-"):
            out.setdefault(cur, set()).add(old_no)
            old_no += 1
        elif line.startswith("+"):
            out.setdefault(cur, set()).add(max(old_no - 1, 1))   # insertion: attribute to the line above
        else:
            old_no += 1
    return {f: s for f, s in out.items() if not re.search(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$", f)}


def touches_module_level(source: str, lines: set) -> bool:
    """True when some changed lines are outside every function/method (imports, constants, decorators...)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inside.update(range(node.lineno, node.end_lineno + 1))
    return any(ln not in inside for ln in lines)


def enclosing_functions(source: str, path: str, lines: set) -> list:
    """Qualified chunk ids (path::Qual.name) of the functions/methods enclosing the given lines."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    fn_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    spans = []
    for node in tree.body:
        if isinstance(node, fn_types):
            spans.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            spans.extend((f"{node.name}.{m.name}", m) for m in node.body if isinstance(m, fn_types))
    for qual, node in spans:
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        if any(start <= ln <= node.end_lineno for ln in lines):
            found.append(f"{path}::{qual}")
    if not found:
        found.append(f"{path}::<module>")
    return found


def _strip_env_noise(output: str) -> str:
    """Drop machine-specific header lines (platform, rootdir, plugins) and site-packages warnings."""
    keep = []
    for ln in output.splitlines():
        if ln.startswith(("platform ", "rootdir:", "plugins:", "benchmark:", "cachedir:")):
            continue
        if "site-packages" in ln:
            continue
        keep.append(ln)
    return "\n".join(keep).strip()


def export_commit(clone: str, commit: str, dest: str) -> None:
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    fd, tar_path = tempfile.mkstemp(suffix=".tar")
    os.close(fd)
    try:
        subprocess.run(["git", "-C", clone, "archive", "--format=tar", "-o", tar_path, commit], check=True)
        with tarfile.open(tar_path) as tf:
            if hasattr(tarfile, "data_filter"):
                tf.extractall(dest, filter="data")
            else:                                        # pragma: no cover - Python < 3.10.12
                tf.extractall(dest)
    finally:
        os.remove(tar_path)


def git_apply(dest: str, patch_path: str, reverse: bool) -> None:
    """Apply a patch to a plain directory. GIT_CEILING_DIRECTORIES stops git from treating an enclosing
    repository (e.g. this project, when cases live under data/) as the patch root, which would silently
    skip every file."""
    cmd = ["git", "apply", *(["-R"] if reverse else []), os.path.abspath(patch_path)]
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": os.path.dirname(os.path.abspath(dest))}
    subprocess.run(cmd, cwd=dest, check=True, capture_output=True, env=env)


def build_case(bug_dir: str, clone: str, project: str, bug_id: int, out_dir: str, env_name: str, python: str,
               timeout: int = 120) -> dict:
    info = read_info(os.path.join(bug_dir, "bug.info"))
    with open(os.path.join(bug_dir, "run_test.sh"), encoding="utf-8") as fh:
        test_cmd = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")][-1]
    patch_path = os.path.join(bug_dir, "bug_patch.txt")
    rel_repo = f"repos/{project}-{bug_id}"
    dest = os.path.join(out_dir, rel_repo)
    export_commit(clone, info["fixed_commit_id"], dest)
    git_apply(dest, patch_path, reverse=True)                     # buggy source + regression test
    # tests run in temporary copies (as the agent's tools do), so no enclosing project config leaks in
    buggy = run_failing_test(dest, None, timeout, 30, python, test_cmd)
    git_apply(dest, patch_path, reverse=False)
    fixed = run_failing_test(dest, None, timeout, 30, python, test_cmd)
    git_apply(dest, patch_path, reverse=True)
    with open(patch_path, encoding="utf-8", errors="replace") as fh:
        changed = patch_changed_lines(fh.read())
    gold_funcs, module_level = [], False
    for path, lines in changed.items():
        src_file = os.path.join(dest, path)
        if os.path.exists(src_file):
            with open(src_file, encoding="utf-8", errors="replace") as fh:
                src = fh.read()
            gold_funcs.extend(enclosing_functions(src, path, lines))
            module_level = module_level or touches_module_level(src, lines)
    target = test_cmd.split()[-1]
    output = _strip_env_noise(buggy["output"])
    report = (f"The regression test `{target}` fails on the current code.\n\n"
              f"Failing test output (last lines):\n{output}")
    return {"id": f"{project}-{bug_id}", "dataset": f"bugsinpy-{project}", "repo": rel_repo, "bug_report": report,
            "test_cmd": test_cmd, "env": env_name,
            "gold": {"files": sorted(changed), "functions": gold_funcs, "module_level_change": module_level},
            "source": {"bugsinpy": f"{project}/{bug_id}", "buggy_commit": info.get("buggy_commit_id"),
                       "fixed_commit": info.get("fixed_commit_id"),
                       "reproduced": (not buggy["passed"]) and fixed["passed"],
                       "buggy_result": buggy["output"].splitlines()[-1:] or [""],
                       "fixed_result": fixed["output"].splitlines()[-1:] or [""]}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a BugsInPy case manifest")
    ap.add_argument("--bugsinpy", required=True, help="path to a BugsInPy checkout")
    ap.add_argument("--project", required=True)
    ap.add_argument("--clone", required=True, help="full git clone of the project")
    ap.add_argument("--out", default="data/real")
    ap.add_argument("--bugs", default="1-5", help="e.g. 1-32 or 1,4,7")
    ap.add_argument("--env", action="append", default=[], help="NAME=PYTHON (repeatable)")
    ap.add_argument("--env-for", action="append", default=[], help="BUGS=NAME, e.g. 3-21=py38-pytest3")
    ap.add_argument("--default-env", required=True)
    a = ap.parse_args(argv)
    envs = dict(e.split("=", 1) for e in a.env)
    env_of = {}
    for spec in a.env_for:
        bugs, name = spec.split("=", 1)
        env_of.update({b: name for b in _ranges(bugs)})
    os.makedirs(a.out, exist_ok=True)
    manifest = os.path.join(a.out, f"{a.project}.jsonl")
    rows = []
    for b in _ranges(a.bugs):
        env_name = env_of.get(b, a.default_env)
        row = build_case(os.path.join(a.bugsinpy, "projects", a.project, "bugs", str(b)), a.clone, a.project, b,
                         a.out, env_name, envs[env_name])
        rows.append(row)
        print(f"{row['id']}: reproduced={row['source']['reproduced']} gold={row['gold']['functions']}")
    with open(manifest, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"saved {manifest} ({sum(r['source']['reproduced'] for r in rows)}/{len(rows)} reproduced)")


if __name__ == "__main__":
    main()
