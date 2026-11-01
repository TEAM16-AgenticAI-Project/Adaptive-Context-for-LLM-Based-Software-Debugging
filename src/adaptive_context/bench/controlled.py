"""Build/validate eight deliberately lexical-mismatch debugging stress cases.

Gold patches live outside each indexed repository. Validation uses gold only
inside temporary copies and never calls a model or changes the buggy fixtures.
"""
from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from ..cases import load_cases
from ..tools import IGNORE, run_failing_test, run_pytest_in
from .audit import revision
from .bugsinpy import git_apply

SPECS = [
    ("receipt", "Checkout receipt charges the wrong amount for several units.", "value + 5", "value * 5", [(3, 15), (2, 10)]),
    ("admission", "Admission screen rejects a visitor exactly at the age threshold.", "value > 18", "value >= 18",
     [(18, True), (17, False), (19, True)]),
    ("levy", "Invoice levy shown by checkout disagrees with the one fifth rate.", "value // 4", "value // 5",
     [(100, 20), (50, 10)]),
    ("session", "Session access remains active at the expiration boundary.", "value > 3600", "value >= 3600",
     [(3600, True), (3599, False), (3601, True)]),
    ("stock", "Inventory checkout displays negative stock after the last item.", "value - 1", "max(0, value - 1)",
     [(0, 0), (2, 1)]),
    ("label", "Label preview leaves trailing blank space in the receipt.", "value.lstrip()", "value.strip()",
     [("  ready  ", "ready"), ("ok", "ok")]),
    ("selection", "Selection screen chooses the second option instead of the first.", "value[1]", "value[0]",
     [(["a", "b"], "a"), ([4, 8], 4)]),
    ("queue", "Queue display puts the largest ticket first instead of ascending order.", "sorted(value, reverse=True)",
     "sorted(value)", [([3, 1, 2], [1, 2, 3]), ([8, 2], [2, 8])]),
]


def build(out: str) -> str:
    root = Path(out)
    rows = []
    for index, (entry, report, buggy, fixed, examples) in enumerate(SPECS, 1):
        case_id = f"controlled-{index:02}"
        repo = root / "repos" / case_id
        repo.mkdir(parents=True, exist_ok=True)
        gateway = f'from relay import route\n\ndef {entry}(value):\n    """{report}"""\n    return route(value)\n'
        (repo / "gateway.py").write_text(gateway)
        (repo / "relay.py").write_text("from kernel import fold\n\ndef route(value):\n    return fold(value)\n")
        old = f"def fold(value):\n    return {buggy}\n"
        new = f"def fold(value):\n    return {fixed}\n"
        (repo / "kernel.py").write_text(old)
        decoys = "\n\n".join(f'def {entry}_{suffix}(value):\n    """{report}"""\n    return value'
                                 for suffix in ("preview", "status", "details", "history")) + "\n"
        (repo / "screens.py").write_text(decoys)
        test = f"from gateway import {entry}\n\ndef test_flow():\n"
        test += "".join(f"    assert {entry}({value!r}) == {expected!r}\n" for value, expected in examples)
        (repo / "test_flow.py").write_text(test)
        patch = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                             fromfile="a/kernel.py", tofile="b/kernel.py"))
        (root / "patches").mkdir(exist_ok=True)
        (root / "patches" / f"{case_id}.patch").write_text(patch)
        rows.append({"id": case_id, "dataset": "controlled-lexical-mismatch", "repo": f"repos/{case_id}",
                     "bug_report": report, "test_cmd": "pytest -q test_flow.py", "env": "current",
                     "gold": {"files": ["kernel.py"], "functions": ["kernel.py::fold"]},
                     "source": {"kind": "hand-authored template stress case", "generator_revision": revision(),
                                "gold_patch": f"patches/{case_id}.patch", "held_out": False}})
    manifest = root / "cases.jsonl"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return str(manifest)


def validate(manifest: str, out: str, python: str | None = None) -> dict:
    records = []
    base = Path(manifest).resolve().parent
    for case in load_cases(manifest):
        before = run_failing_test(case.repo, test_cmd=case.test_cmd, python=python or sys.executable)
        with tempfile.TemporaryDirectory(prefix="controlled-gold-") as directory:
            work = Path(directory) / "repo"
            shutil.copytree(case.repo, work, ignore=IGNORE)
            patch = (base / case.source["gold_patch"]).resolve()
            if not patch.is_relative_to(base):
                raise ValueError("gold patch must remain inside the dataset")
            git_apply(str(work), str(patch), reverse=False)
            after = run_pytest_in(str(work), test_cmd=case.test_cmd, python=python or sys.executable)
        # Pytest exit 1 is test failure; import/collection errors and timeouts do
        # not establish a reproduced bug even when a later patch happens to pass.
        reproduced = before["returncode"] == 1 and not before["timed_out"] and after["passed"]
        records.append({"case": case.id, "buggy": before, "fixed": after, "reproduced": reproduced})
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "validation.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))
    summary = {"revision": revision(), "manifest": manifest, "cases": len(records),
               "reproduced": sum(row["reproduced"] for row in records), "llm_calls": 0}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--manifest", default="data/controlled/cases.jsonl")
    parser.add_argument("--out", default="results/deep/controlled")
    args = parser.parse_args(argv)
    manifest = build(str(Path(args.manifest).parent)) if args.build else args.manifest
    result = validate(manifest, args.out)
    print(json.dumps(result, indent=2))
    if result["cases"] != result["reproduced"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
