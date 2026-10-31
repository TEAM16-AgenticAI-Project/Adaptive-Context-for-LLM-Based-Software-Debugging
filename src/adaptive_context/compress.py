"""Context compression. Level 0 full, 1 trimmed, 2 outline, 3 signature.

The first docstring line is kept at every level (it states the intended behaviour,
which is key evidence for a bug). Level 1 strips comments and long docstrings.
Level 2 keeps the signature plus return/raise/assert lines and lines with focus terms.
"""
from __future__ import annotations

import ast
import textwrap

from .models import Chunk

LEVELS = ("full", "trimmed", "outline", "signature")
_FN = (ast.FunctionDef, ast.AsyncFunctionDef)


def _doc_node(fn):
    b = fn.body
    if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0], "value", None), ast.Constant) \
            and isinstance(b[0].value.value, str):
        return b[0]
    return None


def _first_line(s: str) -> str:
    s = s.strip()
    return s.splitlines()[0] if s else ""


def _line_trunc(text: str, level: int) -> str:
    keep = {1: 30, 2: 12, 3: 3}[level]
    lines = text.splitlines()
    return text if len(lines) <= keep else "\n".join(lines[:keep] + ["# ... truncated"])


def compress(chunk: Chunk, level: int, focus=()) -> str:
    level = max(0, min(3, level))
    if level == 0:
        return chunk.text
    src = textwrap.dedent(chunk.text)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return _line_trunc(chunk.text, level)
    if not tree.body or not isinstance(tree.body[0], _FN):
        return _line_trunc(chunk.text, level)
    fn = tree.body[0]
    doc = _doc_node(fn)
    if level == 1:
        if doc is not None:
            doc.value.value = _first_line(doc.value.value)
        return ast.unparse(tree)
    lines = src.splitlines()
    body_start = fn.body[0].lineno - 1
    head = lines[fn.lineno - 1:body_start]
    if doc is not None:
        head.append(f'    """{_first_line(doc.value.value)}"""')
        rest = fn.body[1:]
    else:
        rest = fn.body
    if level == 3:
        return "\n".join(head + ["    ..."])
    focus = [f.lower() for f in focus if f]
    kept, skipped = [], False
    end = fn.end_lineno
    first_rest = rest[0].lineno - 1 if rest else end
    for ln in lines[first_rest:end]:
        s = ln.strip()
        if s.startswith(("return", "raise", "yield", "assert")) or any(f in ln.lower() for f in focus):
            kept.append(ln)
        elif s:
            skipped = True
    if skipped:
        kept.insert(0, "    ...")
    return "\n".join(head + kept)
