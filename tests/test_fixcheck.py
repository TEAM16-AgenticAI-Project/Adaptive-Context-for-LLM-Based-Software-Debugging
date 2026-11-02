"""Fix check (Tanay): apply a model-written fix in a temporary copy and rerun the failing test."""
import hashlib
import os
import shutil

import pytest

from adaptive_context.fixcheck import CHECK_REASONS, FixError, apply_fix_to_source, check_fix, is_diff, strip_fences

HERE = os.path.dirname(os.path.abspath(__file__))
PAYMENT = os.path.join(HERE, "..", "examples", "payment_bug")
TEST = "test_payment.py"

GOOD = '''def checkout(price, discount):
    """Return the amount the customer pays."""
    return apply_discount(price, discount)
'''
WRONG = '''def checkout(price, discount):
    """Return the amount the customer pays."""
    return price - discount
'''


@pytest.fixture
def repo(tmp_path):
    dst = tmp_path / "payment_bug"
    shutil.copytree(PAYMENT, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return str(dst)


def _digest(root):
    h = hashlib.sha256()
    for dp, dns, fns in sorted(os.walk(root)):
        dns.sort()
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            h.update(os.path.relpath(p, root).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


# ------------------------------------------------------------------ check_fix on examples/payment_bug
def test_correct_function_fix_passes(repo):
    r = check_fix(repo, "payment.py", "checkout", GOOD, test_path=TEST)
    assert r == {**r, "applied": True, "mode": "function", "passed": True, "reason": "passed"}
    assert set(r) == {"applied", "mode", "passed", "reason", "output"}


def test_wrong_fix_fails_with_tests_fail(repo):
    r = check_fix(repo, "payment.py", "checkout", WRONG, test_path=TEST)
    assert r["applied"] and not r["passed"] and r["reason"] == "tests_fail"
    assert "assert" in r["output"]


def test_line_mode(repo):
    r = check_fix(repo, "payment.py", "checkout", "price = apply_discount(price, discount)", test_path=TEST)
    assert (r["mode"], r["passed"], r["reason"]) == ("line", True, "passed")


def test_fenced_fix_and_test_cmd(repo):
    r = check_fix(repo, "payment.py", "checkout", f"Here is the fix:\n```python\n{GOOD}```\n",
                  test_cmd=f"pytest -q {TEST}")
    assert r["passed"], r["output"]


def test_original_files_untouched(repo):
    before = _digest(repo)
    check_fix(repo, "payment.py", "checkout", GOOD, test_path=TEST)
    check_fix(repo, "payment.py", "checkout", WRONG, test_path=TEST)
    check_fix(repo, "payment.py", "checkout", DIFF, test_path=TEST)
    assert _digest(repo) == before


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "sub/../../x.py"])
def test_path_outside_repo_refused(repo, path):
    r = check_fix(repo, path, "checkout", GOOD, test_path=TEST)
    assert (r["applied"], r["passed"], r["reason"]) == (False, False, "path_outside_repo")


def test_missing_file(repo):
    assert check_fix(repo, "nope.py", "checkout", GOOD)["reason"] == "file_not_found"


def test_function_not_found(repo):
    r = check_fix(repo, "payment.py", "refund", GOOD, test_path=TEST)
    assert (r["applied"], r["reason"]) == (False, "function_not_found")


def test_unchanged_body_rejected(repo):
    with open(os.path.join(repo, "payment.py")) as fh:
        src = fh.read()
    body = src[src.index("def checkout"):]
    r = check_fix(repo, "payment.py", "checkout", body + "\n# looks fine\n", test_path=TEST)
    assert (r["applied"], r["reason"]) == (False, "no_change")


def test_timeout(repo):
    slow = "def checkout(price, discount):\n    while True:\n        pass\n"
    r = check_fix(repo, "payment.py", "checkout", slow, test_path=TEST, timeout=3)
    assert (r["applied"], r["passed"], r["reason"]) == (True, False, "timeout")


# ------------------------------------------------------------------ diff fixes (multi-function / module level)
DIFF = """diff --git a/payment.py b/payment.py
--- a/payment.py
+++ b/payment.py
@@ -3,5 +3,4 @@ from discount import apply_discount

 def checkout(price, discount):
     \"\"\"Return the amount the customer pays.\"\"\"
-    apply_discount(price, discount)
-    return price
+    return apply_discount(price, discount)
"""


def test_diff_fix_applies(repo):
    assert is_diff(DIFF)
    r = check_fix(repo, "payment.py", "checkout", DIFF, test_path=TEST)
    assert (r["applied"], r["mode"], r["passed"]) == (True, "diff", True), r["output"]


def test_diff_without_prefixes(repo):
    diff = DIFF.replace("a/payment.py", "payment.py").replace("b/payment.py", "payment.py").split("\n", 1)[1]
    r = check_fix(repo, "payment.py", "checkout", diff, test_path=TEST)
    assert r["passed"], r["output"]


def test_diff_that_does_not_apply(repo):
    r = check_fix(repo, "payment.py", "checkout", DIFF.replace("-    return price", "-    return prices"),
                  test_path=TEST)
    assert (r["applied"], r["reason"]) == (False, "diff_does_not_apply")


def test_diff_outside_repo_refused(repo):
    r = check_fix(repo, "payment.py", "checkout", DIFF.replace("payment.py", "../payment.py"), test_path=TEST)
    assert r["reason"] == "path_outside_repo"


def test_diff_touching_two_files(repo):
    diff = DIFF + """diff --git a/discount.py b/discount.py
--- a/discount.py
+++ b/discount.py
@@ -1,3 +1,4 @@
+ROUND = 2
 def apply_discount(price, discount):
     \"\"\"Return price after a fractional discount (0.20 = 20% off).\"\"\"
-    return price * (1 - discount)
+    return round(price * (1 - discount), ROUND)
"""
    r = check_fix(repo, "payment.py", "checkout", diff, test_path=TEST)
    assert r["passed"], r["output"]


# ------------------------------------------------------------------ apply_fix_to_source (pure)
SRC = '''"""Module doc."""
import os
from typing import List


def helper(x):
    return x + 1


class Cart:
    @property
    def total(self):
        return sum(self.items)

    @staticmethod
    def tax(x):
        return x * 0.1


def checkout(price):
    total = price
    return total
'''


def _reason(fix, fn="checkout", src=SRC):
    with pytest.raises(FixError) as e:
        apply_fix_to_source(src, fn, fix)
    return e.value.reason


def test_import_hoisting():
    fix = "import re\nimport os\nfrom typing import List\n\ndef checkout(price):\n    return re.sub('x', '', str(price))\n"
    new, mode = apply_fix_to_source(SRC, "checkout", fix)
    lines = new.splitlines()
    assert mode == "function"
    assert lines[1:4] == ["import os", "from typing import List", "import re"]     # after the last top import
    assert new.count("import os") == 1 and new.count("from typing import List") == 1
    assert "return re.sub" in new and "total = price" not in new


def test_import_hoisting_without_existing_imports():
    src = '"""Doc."""\n\n\ndef f(x):\n    return x\n'
    new, _ = apply_fix_to_source(src, "f", "import math\ndef f(x):\n    return math.floor(x)\n")
    assert new.splitlines()[:2] == ['"""Doc."""', "import math"]


def test_qualified_method_keeps_decorator():
    new, mode = apply_fix_to_source(SRC, "Cart.total", "def total(self):\n    return sum(self.items) or 0\n")
    assert mode == "function"
    assert "    @property\n    def total(self):\n        return sum(self.items) or 0\n" in new


def test_decorated_fix_replaces_decorators():
    new, _ = apply_fix_to_source(SRC, "cart.py::Cart.tax", "@classmethod\ndef tax(cls, x):\n    return x * 0.2\n")
    assert "@staticmethod" not in new and "    @classmethod\n    def tax(cls, x):" in new


def test_async_function_and_dotted_name():
    new, _ = apply_fix_to_source(SRC, "pkg.mod.helper", "async def helper(x):\n    return x + 2\n")
    assert "async def helper(x):\n    return x + 2" in new


def test_line_mode_keeps_indent_and_needs_similarity():
    new, mode = apply_fix_to_source(SRC, "Cart.tax", "return x * 0.08")
    assert mode == "line" and "        return x * 0.08\n" in new
    assert _reason("raise SystemExit('totally unrelated')", "Cart.tax") == "no_similar_line"


@pytest.mark.parametrize("fix,reason", [
    ("", "empty_fix"),
    ("```python\n```", "empty_fix"),
    ("def checkout(price):\n    return (price\n", "fix_syntax_error"),
    ("class Checkout:\n    pass\n", "unsupported_fix_shape"),
    ("x = 1\ny = 2\n", "unsupported_fix_shape"),
    ("def checkout(p):\n    return p\n\ndef helper(x):\n    return x\n", "unsupported_fix_shape"),
    ("import re", "unsupported_fix_shape"),
    ("def refund(p):\n    return p\n", "unsupported_fix_shape"),
    ("def checkout(price):\n    total = price\n    return total\n", "no_change"),
    ("total = price", "no_change"),
    ("def checkout(price):\n  return price\n    return 1\n", "fix_syntax_error"),
])
def test_rejected_shapes(fix, reason):
    assert _reason(fix) == reason


def test_function_not_found_and_ambiguous():
    assert _reason("def nope():\n    pass\n", "nope") == "function_not_found"
    src = "class A:\n    def run(self):\n        return 1\n\n\nclass B:\n    def run(self):\n        return 2\n"
    assert _reason("def run(self):\n    return 3\n", "run", src) == "function_not_found"
    new, _ = apply_fix_to_source(src, "B.run", "def run(self):\n    return 3\n")
    assert "return 1" in new and "return 3" in new and "return 2" not in new


def test_fix_naming_a_sibling_method_is_applied_there():
    new, _ = apply_fix_to_source(SRC, "Cart.total", "def tax(x):\n    return x * 0.2\n")
    assert "return x * 0.2" in new and "return sum(self.items)" in new


def test_strip_fences_and_reason_list():
    assert strip_fences("text\n```py\nx = 1\n```\nmore") == "x = 1\n"
    assert {"passed", "tests_fail", "timeout", "no_change", "path_outside_repo"} <= set(CHECK_REASONS)
