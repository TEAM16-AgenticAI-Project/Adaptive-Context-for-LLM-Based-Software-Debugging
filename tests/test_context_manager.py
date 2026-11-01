"""Context manager: chunking, lookup, import-aware callees, budget packing, notes, adaptive loop bookkeeping."""
import warnings

from adaptive_context.chunking import chunk_source


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
