"""audit.py --validate-fixcheck (Tanay): gold fixes from a maintainer patch, negative control, CLI."""
import json
import os
import shutil

import pytest

from adaptive_context.bench import audit
from adaptive_context.cases import RealCase

HERE = os.path.dirname(os.path.abspath(__file__))
PAYMENT = os.path.join(HERE, "..", "examples", "payment_bug")

ONE_FUNC = """diff --git a/payment.py b/payment.py
--- a/payment.py
+++ b/payment.py
@@ -3,5 +3,4 @@ from discount import apply_discount

 def checkout(price, discount):
     \"\"\"Return the amount the customer pays.\"\"\"
-    apply_discount(price, discount)
-    return price
+    return apply_discount(price, discount)
"""
WITH_IMPORT = """diff --git a/payment.py b/payment.py
--- a/payment.py
+++ b/payment.py
@@ -1,7 +1,7 @@
+import math
 from discount import apply_discount


 def checkout(price, discount):
     \"\"\"Return the amount the customer pays.\"\"\"
-    apply_discount(price, discount)
-    return price
+    return math.floor(apply_discount(price, discount))
"""
MODULE_LEVEL = """diff --git a/payment.py b/payment.py
--- a/payment.py
+++ b/payment.py
@@ -1,7 +1,7 @@
 from discount import apply_discount
+FACTOR = 1


 def checkout(price, discount):
     \"\"\"Return the amount the customer pays.\"\"\"
-    apply_discount(price, discount)
-    return price
+    return apply_discount(price, discount) * FACTOR
"""
TEST_ONLY = """diff --git a/tests/test_x.py b/tests/test_x.py
--- a/tests/test_x.py
+++ b/tests/test_x.py
@@ -1 +1 @@
-a
+b
"""


@pytest.fixture
def case(tmp_path):
    dst = tmp_path / "payment_bug"
    shutil.copytree(PAYMENT, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return RealCase("pay-1", "toy", str(dst), "report", ["payment.py"], ["payment.py::checkout"],
                    test_cmd="pytest -q test_payment.py")


def test_split_patch_drops_test_files():
    assert list(audit.split_patch(ONE_FUNC + TEST_ONLY)) == ["payment.py"]


def test_single_function_gold_fix(case):
    g = audit.gold_fix(case, ONE_FUNC)
    assert (g["form"], g["why"]) == ("function", "single_function")
    assert g["fix"].startswith("def checkout") and "return apply_discount" in g["fix"]
    assert g["negative"].startswith("def checkout") and "return price" in g["negative"]


def test_imports_plus_function_gold_fix(case):
    g = audit.gold_fix(case, WITH_IMPORT)
    assert (g["form"], g["why"]) == ("function", "imports+function")
    assert g["fix"].startswith("import math\n")


def test_module_level_change_uses_the_diff(case):
    g = audit.gold_fix(case, MODULE_LEVEL)
    assert (g["form"], g["why"]) == ("diff", "module_level") and g["fix"].startswith("diff --git")


def test_multi_function_uses_the_diff(case):
    case.gold_functions.append("payment.py::refund")
    assert audit.gold_fix(case, ONE_FUNC)["why"] == "multi_function"


def test_same_module_ignores_import_placement():
    a = "import os\nimport re\n\nx = 1\n"
    assert audit._same_module(a, "import re\nimport os\nx = 1\n")
    assert audit._same_module("from m import a, b\n", "from m import a\nfrom m import a, b\n")
    assert not audit._same_module(a, "import os\nx = 1\n")
    assert not audit._same_module(a, "import os\nimport re\nx = 2\n")


@pytest.mark.parametrize("patch", [ONE_FUNC, WITH_IMPORT, MODULE_LEVEL])
def test_validate_case_end_to_end(case, patch):
    r = audit.validate_case(case, patch, None, timeout=60)
    assert r["gold"]["passed"], r
    assert r["negative"] == {"applied": False, "mode": "", "passed": False, "reason": "no_change"}
    assert r["baseline_failed"] and r["ok"]


def test_summary_and_markdown(case):
    recs = [audit.validate_case(case, ONE_FUNC, None, 60), audit.validate_case(case, MODULE_LEVEL, None, 60)]
    s = audit.summarize(recs, {"commit": "abc", "src_dirty": False})
    assert (s["cases"], s["gold_passed"], s["negative_rejected"], s["all_ok"], s["not_ok"]) == (2, 2, 2, 2, [])
    assert s["by_form"] == {"function": {"cases": 1, "gold_passed": 1}, "diff": {"cases": 1, "gold_passed": 1}}
    md = audit.markdown(recs, s)
    assert "| pay-1 | function |" in md and "2/2" in md
    assert audit.markdown_fixcheck(recs, s) == md
    assert audit.summarize_audit([])["processed"] == 0
    json.dumps(s)


def test_cli_dispatches_to_deeps_implemented_audit(monkeypatch, tmp_path):
    calls = []

    def run_audit(manifest, out, **kwargs):
        calls.append((manifest, out, kwargs))
        return {"errors": 0, "processed": 1}

    monkeypatch.setattr(audit, "run_audit", run_audit)
    assert audit.main(["--audit", "--manifest", "cases.jsonl", "--out", str(tmp_path), "--limit", "1"]) == 0
    assert calls[0][0] == "cases.jsonl" and calls[0][2]["limit"] == 1
    assert audit.main(["--audit"]) == 0
    assert calls[1][0] == audit.DEFAULT_MANIFEST


def test_cli_dispatches_to_fixcheck_and_resolves_env_paths(monkeypatch, tmp_path):
    calls = []

    def validate(*args, **kwargs):
        calls.append((args, kwargs))
        return {"revision": {}, "all_ok": 1, "cases": 1}

    monkeypatch.setattr(audit, "validate_fixcheck", validate)
    assert audit.main(["--validate-fixcheck", "--bugsinpy", str(tmp_path), "--cases", "one",
                       "--env", "current=relative/python", "--repo-root", str(tmp_path)]) == 0
    assert os.path.isabs(calls[0][0][2]["current"])
    assert calls[0][0][5] == {"one"} and calls[0][1]["repo_root"] == str(tmp_path)


def test_cli_preserves_virtualenv_interpreter_symlink(monkeypatch, tmp_path):
    base = tmp_path / "base-python"
    base.write_text("# interpreter target fixture")
    virtualenv = tmp_path / "venv-python"
    virtualenv.symlink_to(base)
    calls = []

    def validate(*args, **kwargs):
        calls.append(args)
        return {"revision": {}, "all_ok": 0, "cases": 0}

    monkeypatch.setattr(audit, "validate_fixcheck", validate)
    assert audit.main(["--validate-fixcheck", "--bugsinpy", str(tmp_path),
                       "--env", f"current={virtualenv}"]) == 0
    assert calls[0][2]["current"] == str(virtualenv)


def test_cli_needs_bugsinpy():
    with pytest.raises(SystemExit):
        audit.main(["--validate-fixcheck"])
