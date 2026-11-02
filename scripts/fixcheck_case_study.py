"""Fix-check case study on one real bug (report Section 4, and the limit shown in Section 5).

Runs check_fix on thefuck-5 with four fixes and writes results/fixcheck_case_<id>.json:
  1. maintainer   the maintainer's fixed function (built from BugsInPy, evaluator-only data)
  2. plausible    a reasonable but wrong fix: a word-boundary regex, which still matches the branch name
  3. unchanged    the buggy function resubmitted
  4. overfitted   a fix that special-cases the test's input; it passes the regression test (Section 5)
Each fix is also run against the whole test module, not just the regression test.

  python scripts/fixcheck_case_study.py --bugsinpy ../m2-work/BugsInPy \\
      --env py38-pytest6=../m2-work/venv38/bin/python --env py38-pytest3=../m2-work/venv38old/bin/python
"""
from __future__ import annotations

import argparse
import json
import os

from adaptive_context.bench.audit import DEFAULT_MANIFEST, ROOT, _revision, gold_fix
from adaptive_context.cases import load_cases
from adaptive_context.fixcheck import check_fix

PLAUSIBLE = """@git_support
def match(command):
    return ('push' in command.script_parts
            and re.search(r'\\bset-upstream\\b', command.output) is not None)
"""
OVERFITTED = """@git_support
def match(command):
    return ('push' in command.script_parts
            and 'set-upstream' in command.output
            and 'bitbucket' not in command.output)
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bugsinpy", required=True)
    ap.add_argument("--case", default="thefuck-5")
    ap.add_argument("--env", action="append", default=[])
    ap.add_argument("--out", default=os.path.join(ROOT, "results"))
    a = ap.parse_args(argv)
    envs = dict(e.split("=", 1) for e in a.env)
    case = next(c for c in load_cases(DEFAULT_MANIFEST) if c.id == a.case)
    bug = case.source["bugsinpy"].split("/")[-1]
    with open(os.path.join(a.bugsinpy, "projects", "thefuck", "bugs", bug, "bug_patch.txt"), encoding="utf-8") as fh:
        g = gold_fix(case, fh.read())
    fixes = {"maintainer": g["fix"], "plausible": PLAUSIBLE, "unchanged": g["negative"], "overfitted": OVERFITTED}
    module_cmd = "pytest " + case.test_cmd.split()[-1].split("::")[0]
    python = envs.get(case.env)
    print(f"case {case.id}  target {g['file']}::{g['function']}  env {case.env}")
    print(f"regression test: {case.test_cmd}\nwhole module:    {module_cmd}\n")
    rows = []
    for name, fix in fixes.items():
        reg = check_fix(case.repo, g["file"], g["function"], fix, test_cmd=case.test_cmd, python=python)
        mod = check_fix(case.repo, g["file"], g["function"], fix, test_cmd=module_cmd, python=python)
        last = (reg["output"].strip().splitlines() or [""])[-1]
        rows.append({"fix": name, "code": fix, "regression": {k: reg[k] for k in ("applied", "mode", "passed", "reason")},
                     "regression_last_line": last,
                     "module": {k: mod[k] for k in ("applied", "mode", "passed", "reason")},
                     "module_last_line": (mod["output"].strip().splitlines() or [""])[-1]})
        print(f"--- {name} fix")
        print("\n".join("    " + ln for ln in fix.rstrip().splitlines()))
        print(f"  regression test -> applied={reg['applied']} mode={reg['mode'] or '-'} passed={reg['passed']} "
              f"reason={reg['reason']}")
        if last:
            print(f"    {last}")
        print(f"  whole module    -> passed={mod['passed']} reason={mod['reason']}\n")
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"fixcheck_case_{case.id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"revision": _revision(), "case": case.id, "target": f"{g['file']}::{g['function']}",
                   "test_cmd": case.test_cmd, "module_cmd": module_cmd, "rows": rows}, fh, indent=2)
    print(f"saved {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
