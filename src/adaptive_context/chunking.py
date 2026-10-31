"""Split Python source into function/method chunks (AST based), with line-window fallback."""
from __future__ import annotations

import ast
import os

from .models import Chunk

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages", "build", "dist", ".tox"}


def _calls(node: ast.AST) -> tuple:
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return tuple(sorted(names))


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


def chunk_source(path: str, source: str, window: int = 40, overlap: int = 5) -> list:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return _windows(path, source, window, overlap)
    lines = source.splitlines()
    out, covered = [], set()

    def add(node, qual, kind):
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
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
    rest = [(i + 1, l) for i, l in enumerate(lines) if (i + 1) not in covered and l.strip()]
    if rest:
        text = "\n".join(l for _, l in rest[:60])
        out.append(Chunk(f"{path}::<module>", path, "<module>", rest[0][0], rest[-1][0], text, "module"))
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
