"""Shared Milestone 2 setup: case manifests, the BugsInPy gold mapping, the test runner, result fields."""
import json
import os
import shutil
import sys

from adaptive_context.cases import load_cases, score_diagnosis
from adaptive_context.models import STOP_REASONS, DebugResult, Diagnosis, RunStats
from adaptive_context.tools import run_failing_test

HERE = os.path.dirname(__file__)
PAYMENT = os.path.join(HERE, "..", "examples", "payment_bug")
MANIFEST = os.path.join(HERE, "..", "data", "real", "thefuck.jsonl")


def test_case_manifest_loads_and_scores(tmp_path):
    shutil.copytree(PAYMENT, tmp_path / "repos" / "pay")
    row = {"id": "pay-1", "dataset": "controlled", "repo": "repos/pay", "bug_report": "checkout returns 100",
           "test_cmd": "pytest test_payment.py",
           "gold": {"files": ["payment.py"], "functions": ["payment.py::checkout"]}}
    (tmp_path / "cases.jsonl").write_text(json.dumps(row) + "\n")
    case = load_cases(str(tmp_path / "cases.jsonl"))[0]
    assert os.path.isdir(case.repo) and case.gold_short_names == {"checkout"}
    good = Diagnosis("./payment.py", "checkout", "c", "", "high", True)
    assert score_diagnosis(case, good, [("payment.py::checkout", 1)]) == \
        {"file_hit": True, "func_hit": True, "evidence": True, "grounded": True}
    assert not score_diagnosis(case, Diagnosis("discount.py", "checkout", "c", "", "high", True), [])["file_hit"]
    assert not score_diagnosis(case, Diagnosis(), [])["file_hit"]               # invalid diagnosis never scores


def test_committed_manifest_is_complete_and_keeps_gold_out_of_reports():
    rows = [json.loads(line) for line in open(MANIFEST)]
    assert len(rows) == 32 and all(r["source"]["reproduced"] for r in rows)
    for r in rows:
        assert r["gold"]["functions"] and r["test_cmd"] and r["env"] in ("py38-pytest6", "py38-pytest3")
        assert "bug_patch" not in r["bug_report"] and r["source"]["fixed_commit"] not in r["bug_report"]


def test_bugsinpy_patch_maps_to_buggy_functions():
    from adaptive_context.bench.bugsinpy import enclosing_functions, patch_changed_lines, touches_module_level
    patch = """diff --git a/pkg/rules/x.py b/pkg/rules/x.py
--- a/pkg/rules/x.py
+++ b/pkg/rules/x.py
@@ -1,6 +1,7 @@
 import re
+import os


 def match(command):
-    return 'a' in command
+    return 'b' in command
diff --git a/tests/test_x.py b/tests/test_x.py
--- a/tests/test_x.py
+++ b/tests/test_x.py
@@ -1,2 +1,3 @@
 def test_x():
+    assert True
     pass
"""
    changed = patch_changed_lines(patch)
    assert list(changed) == ["pkg/rules/x.py"]                        # test files excluded
    buggy = "import re\n\n\ndef match(command):\n    return 'a' in command\n"
    assert enclosing_functions(buggy, "pkg/rules/x.py", changed["pkg/rules/x.py"]) == ["pkg/rules/x.py::match"]
    assert touches_module_level(buggy, changed["pkg/rules/x.py"])      # the new import


def test_test_runner_accepts_an_explicit_command_and_interpreter():
    res = run_failing_test(PAYMENT, test_cmd="pytest -q test_payment.py", python=sys.executable)
    assert not res["passed"] and "assert 100 == 80" in res["output"]
    with open(os.path.join(PAYMENT, "payment.py")) as fh:              # original untouched
        assert "return price" in fh.read()


def test_result_fields_have_safe_defaults():
    r = DebugResult(Diagnosis(), RunStats())
    assert r.stop_reason == "" and r.verification == {} and r.stats.tool_calls == 0 and r.stats.wall_s == 0.0
    assert "diagnosed" in STOP_REASONS and "verify_failed" in STOP_REASONS
