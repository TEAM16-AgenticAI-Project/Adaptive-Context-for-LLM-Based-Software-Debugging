"""Context manager: chunking, lookup, import-aware callees, budget packing, notes, adaptive loop bookkeeping."""
import warnings

from adaptive_context.chunking import chunk_repo, chunk_source
from adaptive_context.retrieval import BM25Retriever


def _long_module(n_consts: int) -> str:
    body = ["import os", "from pkg.util import (", "    helper,", "    other,", ")", ""]
    body += [f"CONST_{i} = {i}" for i in range(n_consts)]
    body += ["", "def f(x):", "    return helper(x)", "", "TAIL = 'end'"]
    return "\n".join(body) + "\n"


def test_long_module_splits_without_losing_lines():
    src = _long_module(150)
    mods = [c for c in chunk_source("m.py", src) if c.kind == "module"]
    assert len(mods) >= 3
    assert mods[0].id == "m.py::<module>"
    assert all(c.id == f"m.py::<module>@L{c.start}" for c in mods[1:])
    assert all(len(c.text.splitlines()) <= 60 for c in mods)
    got = [ln for c in mods for ln in c.text.splitlines()]
    funcs = {ln for c in chunk_source("m.py", src) if c.kind == "function" for ln in c.text.splitlines()}
    want = [ln for ln in src.splitlines() if ln.strip() and ln not in funcs]
    assert got == want                                    # every module line kept, in order
    assert "TAIL = 'end'" in mods[-1].text


def test_module_chunks_break_only_between_statements():
    mods = [c for c in chunk_source("m.py", _long_module(150), module_lines=3) if c.kind == "module"]
    first = mods[0].text.splitlines()
    assert first[0] == "import os" and "from pkg.util import (" not in first     # 5-line import kept whole
    imp = next(c for c in mods if "from pkg.util import (" in c.text)
    assert imp.text.splitlines() == ["from pkg.util import (", "    helper,", "    other,", ")"]


def test_chunking_silences_syntax_warning():
    src = 'import re\n\nPAT = re.compile("\\d+")\n\ndef f():\n    return "\\d"\n'
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        names = {c.name for c in chunk_source("old.py", src)}
    assert {"f", "<module>"} <= names


def test_dotted_calls_recorded():
    src = "import pkg.mod as z\n\ndef f(x):\n    return z.func(x) + self_check(x)\n"
    calls = next(c for c in chunk_source("a.py", src) if c.name == "f").calls
    assert {"func", "z.func", "self_check"} <= set(calls)


# ---- lookup -----------------------------------------------------------------------------------------------------
REPO = {
    "pkg/__init__.py": "",
    "pkg/rules/__init__.py": "",
    "pkg/rules/git_push.py": "from pkg.utils import for_app\n\n\ndef match(command):\n    return for_app(command)\n",
    "pkg/rules/ls_all.py": "def match(command):\n    return 'ls' in command\n",
    "pkg/utils.py": "def for_app(c):\n    return c\n\n\nclass Cache:\n    def get(self, k):\n        return k\n",
    "tests/rules/test_git_push.py": (
        "from pkg.rules.git_push import match\n\n\nclass TestPush:\n    def test_y(self):\n"
        "        assert match('git push')\n\n\ndef test_x():\n    assert match('git push')\n"),
}


def _ids(chunks):
    return [c.id for c in chunks]


def test_every_lookup_form_resolves():
    r = BM25Retriever(chunk_repo(REPO))
    gp = ["pkg/rules/git_push.py::match"]
    assert _ids(r.lookup("pkg.rules.git_push.match")) == gp                 # dotted module path
    assert _ids(r.lookup("rules.git_push.match")) == gp                     # suffix of a module path
    assert _ids(r.lookup("pkg.utils.Cache.get")) == ["pkg/utils.py::Cache.get"]   # pkg.mod.Class.method
    assert _ids(r.lookup("pkg/rules/git_push.py::match")) == gp             # path.py::Qual.name
    assert _ids(r.lookup("./pkg/rules/git_push.py::match")) == gp
    assert _ids(r.lookup("rules/git_push.py::match")) == gp                 # path suffix
    assert _ids(r.lookup("tests/rules/test_git_push.py::TestPush::test_y")) == \
        ["tests/rules/test_git_push.py::TestPush.test_y"]                   # pytest id
    assert _ids(r.lookup("tests/rules/test_git_push.py::test_x[git push]")) == \
        ["tests/rules/test_git_push.py::test_x"]                            # parametrized pytest id
    assert _ids(r.lookup("Cache.get")) == ["pkg/utils.py::Cache.get"]       # Class.method
    assert _ids(r.lookup("`for_app()`")) == ["pkg/utils.py::for_app"]       # short name, decorated
    assert sorted(_ids(r.lookup("match"))) == ["pkg/rules/git_push.py::match", "pkg/rules/ls_all.py::match"]
    assert r.lookup("no.such.thing") == []


def test_lookup_returns_most_specific_tier_not_union():
    r = BM25Retriever(chunk_repo(REPO))
    assert len(r.lookup("pkg.rules.ls_all.match")) == 1
    assert len(r.lookup("pkg/rules/ls_all.py::match")) == 1


# ---- import-aware callees ---------------------------------------------------------------------------------------
def test_import_aware_callees_pick_the_imported_match():
    files = {**REPO,
             "pkg/rules/combo.py": ("from . import ls_all\nfrom .git_push import match as gp_match\n\n\n"
                                    "def run(c):\n    return ls_all.match(c) or gp_match(c)\n"),
             "pkg/runner.py": ("class Runner:\n    def match(self, c):\n        return c\n\n"
                               "    def go(self, c):\n        return self.match(c)\n")}
    r = BM25Retriever(chunk_repo(files))
    test_x = r.by_id["tests/rules/test_git_push.py::test_x"]
    assert _ids(r.callees(test_x)) == ["pkg/rules/git_push.py::match"]          # not every `match`
    assert _ids(r.callees(r.by_id["tests/rules/test_git_push.py::TestPush.test_y"])) == ["pkg/rules/git_push.py::match"]
    assert sorted(_ids(r.callees(r.by_id["pkg/rules/combo.py::run"]))) == \
        ["pkg/rules/git_push.py::match", "pkg/rules/ls_all.py::match"]          # relative + module-attribute imports
    assert _ids(r.callees(r.by_id["pkg/runner.py::Runner.go"])) == ["pkg/runner.py::Runner.match"]   # self.method
    assert _ids(r.callees(r.by_id["pkg/rules/git_push.py::match"])) == ["pkg/utils.py::for_app"]


def test_unresolved_calls_fall_back_to_name_matching():
    r = BM25Retriever(chunk_repo({**REPO, "pkg/x.py": "def f(cmd):\n    return cmd.match(1)\n"}))
    got = sorted(_ids(r.callees(r.by_id["pkg/x.py::f"])))
    assert got == ["pkg/rules/git_push.py::match", "pkg/rules/ls_all.py::match"]
