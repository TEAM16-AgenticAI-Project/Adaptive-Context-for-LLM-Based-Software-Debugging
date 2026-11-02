"""Split Python source into function/method chunks (AST based), with line-window fallback.

Module-level code (imports, constants, class headers, top-level statements) is split into bounded chunks at
statement boundaries, so nothing is truncated and no statement is cut in half. The first module chunk keeps the id
`path::<module>`; later ones are `path::<module>@L<n>` (n = first line of the chunk).
"""
from __future__ import annotations

import ast
import bisect
import os
import warnings

from .models import Chunk

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages", "build", "dist", ".tox"}
MODULE_CHUNK_LINES = 60
MODULE = "<module>"


def _dotted(node) -> str | None:
    """'a.b.c' for a chain of Name/Attribute nodes, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _calls(node: ast.AST) -> tuple:
    """Names called inside `node`: bare names (`f`), attribute names (`func` for `z.func()`) and, when the
    receiver is a plain name chain, the dotted form too (`z.func`), so imports can resolve it."""
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
                dotted = _dotted(f)
                if dotted:
                    names.add(dotted)
    return tuple(sorted(names))


def parse_source(source: str):
    """ast.parse without SyntaxWarning noise (old repos use escapes like '\\d' in plain strings)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        return ast.parse(source)


def _windows(path: str, source: str, window: int, overlap: int) -> list:
    lines = source.splitlines()
    out, i = [], 0
    while i < len(lines):
        j = min(len(lines), i + window)
        out.append(Chunk(f"{path}::L{i + 1}", path, f"L{i + 1}", i + 1, j, "\n".join(lines[i:j]), "window"))
        if j == len(lines):
            break
        i = j - overlap
    return out


def _start(node) -> int:
    return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])


def _module_chunks(path: str, tree, lines: list, covered: set, max_lines: int) -> list:
    """Group the uncovered, non-blank lines into chunks of at most `max_lines` lines, breaking only between
    statements (a single statement longer than `max_lines` stays whole)."""
    rest = [(i + 1, ln) for i, ln in enumerate(lines) if (i + 1) not in covered and ln.strip()]
    if not rest:
        return []
    bounds = {1}
    for node in tree.body:
        bounds.add(_start(node))
        if isinstance(node, ast.ClassDef):
            bounds.update(_start(m) for m in node.body)
    bounds = sorted(bounds)
    units = []                                   # consecutive rest lines of the same statement
    for no, ln in rest:
        seg = bisect.bisect_right(bounds, no) - 1
        if units and units[-1][0] == seg:
            units[-1][1].append((no, ln))
        else:
            units.append((seg, [(no, ln)]))
    groups, cur = [], []
    for _, unit in units:
        if cur and len(cur) + len(unit) > max_lines:
            groups.append(cur)
            cur = []
        cur.extend(unit)
    if cur:
        groups.append(cur)
    out = []
    for k, g in enumerate(groups):
        name = MODULE if k == 0 else f"{MODULE}@L{g[0][0]}"
        out.append(Chunk(f"{path}::{name}", path, name, g[0][0], g[-1][0], "\n".join(ln for _, ln in g), "module"))
    return out


def chunk_source(path: str, source: str, window: int = 40, overlap: int = 5,
                 module_lines: int = MODULE_CHUNK_LINES) -> list:
    try:
        tree = parse_source(source)
    except (SyntaxError, ValueError):
        return _windows(path, source, window, overlap)
    lines = source.splitlines()
    out, covered = [], set()

    def add(node, qual, kind):
        start = _start(node)
        end = node.end_lineno
        out.append(Chunk(f"{path}::{qual}", path, qual, start, end,
                         "\n".join(lines[start - 1:end]), kind, _calls(node)))
        covered.update(range(start, end + 1))

    fn_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, fn_types):
            add(node, node.name, "function")
        elif isinstance(node, ast.ClassDef):
            for m in node.body:
                if isinstance(m, fn_types):
                    add(m, f"{node.name}.{m.name}", "method")
    out.extend(_module_chunks(path, tree, lines, covered, module_lines))
    return out


def chunk_repo(files: dict) -> list:
    chunks = []
    for path in sorted(files):
        chunks.extend(chunk_source(path, files[path]))
    return chunks


def load_repo_dir(root: str, max_bytes: int = 200_000) -> dict:
    files = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                full = os.path.join(dirpath, fn)
                if os.path.getsize(full) <= max_bytes:
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        files[os.path.relpath(full, root).replace(os.sep, "/")] = fh.read()
    return files
