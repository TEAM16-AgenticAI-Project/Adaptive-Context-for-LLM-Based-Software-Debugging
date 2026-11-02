"""Fix check for real repositories: apply a model-written fix to a temporary copy and rerun the failing test.

Accepted fix shapes (``apply_fix_to_source``):

* a complete function (``def``, ``async def`` or decorated) that replaces the named function. The target is found
  by AST, with qualified names such as ``Class.method`` (``path.py::Class.method`` and dotted module prefixes are
  also accepted). Without decorators in the fix, the original decorators are kept.
* leading ``import`` / ``from`` lines followed by one function: the imports go after the module's last top-level
  import, skipping ones that are already there.
* a single line that replaces the most similar line inside the function (difflib ratio >= 0.5).

``check_fix`` also accepts a unified diff (``diff --git`` / ``---``/``+++``/``@@``), for fixes that touch several
functions or module-level code. It is validated with ``git apply --check`` in the temporary copy, then applied.

Markdown code fences are stripped. A fix that leaves the module's AST unchanged (whitespace or comments only) is
rejected as ``no_change``.

Safety: this runs model-written code. It works on a temporary copy, with a timeout and with API keys removed from
the environment (see ``tools.run_pytest_in``). That protects the original files; it is NOT a security sandbox.
"""
from __future__ import annotations

import ast
import difflib
import os
import re
import shutil
import subprocess
import tempfile
import textwrap

from .tools import run_pytest_in, scrub_paths, temp_repo

FIX_ERRORS = ("empty_fix", "function_not_found", "unsupported_fix_shape", "fix_syntax_error", "no_change",
              "no_similar_line")
CHECK_REASONS = FIX_ERRORS + ("passed", "tests_fail", "timeout", "path_outside_repo", "file_not_found",
                              "diff_does_not_apply", "git_unavailable")
LINE_MIN_RATIO = 0.5

_FENCE = re.compile(r"```[^\n]*\n(.*?)(?:```|\Z)", re.S)
_FN = (ast.FunctionDef, ast.AsyncFunctionDef)
_DIFF_PATH = re.compile(r"^(?:---|\+\+\+) (?:[ab]/)?(\S+)", re.M)


class FixError(Exception):
    """The fix could not be applied. ``reason`` is one of ``FIX_ERRORS``."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


# --------------------------------------------------------------------------------------------- parsing helpers
def strip_fences(fix: str) -> str:
    """Return the first fenced code block if there is one, else the text itself."""
    m = _FENCE.search(fix or "")
    return m.group(1) if m else (fix or "")


def is_diff(fix: str) -> bool:
    t = strip_fences(fix).lstrip()
    return t.startswith("diff --git") or (t.startswith("--- ") and "\n+++ " in t and "\n@@" in t)


def _qual_candidates(function: str) -> list:
    """'pkg/mod.py::A.f' -> ['A.f']; 'pkg.mod.A.f' -> ['pkg.mod.A.f', 'mod.A.f', 'A.f', 'f']."""
    name = (function or "").strip().split("::")[-1].strip().rstrip("()")
    parts = [p for p in name.split(".") if p]
    return [".".join(parts[i:]) for i in range(len(parts))]


def _functions(tree: ast.Module) -> dict:
    """{qualified name: node} for functions and methods, including nested classes (not nested functions)."""
    out = {}

    def walk(body, prefix):
        for node in body:
            if isinstance(node, _FN):
                out[prefix + node.name] = node
            elif isinstance(node, ast.ClassDef):
                walk(node.body, prefix + node.name + ".")

    walk(tree.body, "")
    return out


def find_function(tree: ast.Module, function: str):
    """Return (qualified name, node) or raise function_not_found. A short name must be unique in the file."""
    funcs = _functions(tree)
    for cand in _qual_candidates(function):
        if cand in funcs:
            return cand, funcs[cand]
        hits = [q for q in funcs if q.endswith("." + cand)]
        if len(hits) == 1:
            return hits[0], funcs[hits[0]]
        if len(hits) > 1:
            raise FixError("function_not_found", f"{function!r} is ambiguous: {', '.join(sorted(hits))}")
    raise FixError("function_not_found", repr(function))


def _start_line(node) -> int:
    return min([node.lineno] + [d.lineno for d in node.decorator_list])


def _import_key(node) -> str:
    return ast.dump(node)


def _same_ast(a: str, b: str) -> bool:
    try:
        return ast.dump(ast.parse(a)) == ast.dump(ast.parse(b))
    except SyntaxError:
        return False


# --------------------------------------------------------------------------------------------- fix application
def _apply_line(lines: list, node, fix_line: str) -> list:
    best, best_i = 0.0, -1
    for i in range(node.lineno - 1, node.end_lineno):                # the def line itself may be the fix
        cur = lines[i].strip()
        if not cur or cur.startswith("#"):
            continue
        r = difflib.SequenceMatcher(None, cur, fix_line).ratio()
        if r > best:
            best, best_i = r, i
    if best_i < 0 or best < LINE_MIN_RATIO:
        raise FixError("no_similar_line", f"best ratio {best:.2f} < {LINE_MIN_RATIO}")
    old = lines[best_i]
    indent = old[: len(old) - len(old.lstrip())]
    nl = "\n" if old.endswith("\n") else ""
    return lines[:best_i] + [indent + fix_line + nl] + lines[best_i + 1:]


def apply_fix_to_source(source: str, function: str, fix: str) -> tuple:
    """Apply ``fix`` to ``function`` in ``source``. Return (new_source, mode), mode in {function, line}.

    Raises FixError(reason) with reason in FIX_ERRORS."""
    text = textwrap.dedent(strip_fences(fix)).strip("\n")
    if not text.strip():
        raise FixError("empty_fix")
    if is_diff(text):
        raise FixError("unsupported_fix_shape", "a diff is applied to the repository by check_fix")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:                                          # pragma: no cover - repos parse
        raise FixError("unsupported_fix_shape", f"target file does not parse: {e}")
    qual, node = find_function(tree, function)
    lines = source.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    nonblank = [ln for ln in text.splitlines() if ln.strip()]
    first = nonblank[0].lstrip()
    if len(nonblank) == 1 and not first.startswith(("def ", "async def ", "@", "import ", "from ", "class ")):
        new = "".join(_apply_line(lines, node, first.strip()))
        mode = "line"
    else:
        try:
            fix_tree = ast.parse(text)
        except SyntaxError as e:
            raise FixError("fix_syntax_error", f"line {e.lineno}: {e.msg}")
        body = fix_tree.body
        imports = [n for n in body if isinstance(n, (ast.Import, ast.ImportFrom))]
        rest = body[len(imports):]
        if any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in rest) or len(rest) != 1 \
                or not isinstance(rest[0], _FN):
            raise FixError("unsupported_fix_shape",
                           "expected one function, optionally preceded by imports (use a diff for more)")
        fn = rest[0]
        if fn.name != node.name:                                      # model fixed a sibling it named itself
            parent = qual.rsplit(".", 1)[0] + "." if "." in qual else ""
            try:
                qual, node = find_function(tree, parent + fn.name)
            except FixError:
                raise FixError("unsupported_fix_shape", f"fix defines {fn.name!r}, not {node.name!r}")
        fix_lines = text.splitlines()
        fn_text = "\n".join(fix_lines[_start_line(fn) - 1: fn.end_lineno])
        fn_text = textwrap.dedent(fn_text)
        start = (_start_line(node) if fn.decorator_list else node.lineno) - 1
        indent = " " * node.col_offset
        repl = [(indent + ln if ln.strip() else ln).rstrip() + "\n" for ln in fn_text.splitlines()]
        edits = [(start, node.end_lineno, repl)]
        if imports:
            have = {_import_key(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))}
            add, seen = [], set()
            for n in imports:
                k = _import_key(n)
                if k not in have and k not in seen:
                    seen.add(k)
                    add.append(ast.get_source_segment(text, n).strip() + "\n")
            if add:
                top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
                if top:
                    at = max(n.end_lineno for n in top)
                elif tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(
                        getattr(tree.body[0], "value", None), ast.Constant) and isinstance(tree.body[0].value.value, str):
                    at = tree.body[0].end_lineno                          # after the module docstring
                else:
                    at = 0
                edits.append((at, at, add))
        for a, b, repl_lines in sorted(edits, key=lambda e: e[0], reverse=True):
            lines[a:b] = repl_lines
        new = "".join(lines)
        mode = "function"
    try:
        ast.parse(new)
    except SyntaxError as e:
        raise FixError("fix_syntax_error", f"patched file, line {e.lineno}: {e.msg}")
    if _same_ast(new, source):
        raise FixError("no_change", "the fix leaves the code unchanged")
    return new, mode


# --------------------------------------------------------------------------------------------- repository check
def _inside(repo: str, path: str) -> bool:
    root = os.path.realpath(repo)
    full = os.path.realpath(os.path.join(root, path))
    return full == root or full.startswith(root + os.sep)


def _result(applied, mode, passed, reason, output="") -> dict:
    return {"applied": applied, "mode": mode, "passed": passed, "reason": reason, "output": output}


def _diff_paths(diff: str) -> list:
    return sorted({p for p in _DIFF_PATH.findall(diff) if p != "/dev/null"})


def _git_apply(work: str, diff: str, check: bool) -> tuple:
    strip = [] if re.search(r"^\+\+\+ b/", diff, re.M) else ["-p0"]   # `git diff` style a/ b/ prefixes or bare paths
    fd, patch = tempfile.mkstemp(suffix=".patch")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(diff if diff.endswith("\n") else diff + "\n")
    env = {**os.environ, "GIT_CEILING_DIRECTORIES": os.path.dirname(work)}   # never use an enclosing repo
    try:
        p = subprocess.run(["git", "apply", *(["--check"] if check else []), "--recount", *strip, patch], cwd=work, env=env,
                           capture_output=True, text=True, timeout=30)
        return p.returncode == 0, (p.stdout + p.stderr).strip()
    finally:
        os.remove(patch)


def check_fix(repo: str, file: str, function: str, fix: str, *, test_path: str | None = None,
              test_cmd: str | None = None, python: str | None = None, timeout: int = 120) -> dict:
    """Apply ``fix`` to ``file``/``function`` in a temporary copy of ``repo`` and run the failing test there.

    Returns {"applied", "mode", "passed", "reason", "output"}. ``reason`` is one of ``CHECK_REASONS``
    ("passed" on success). The original repository is never modified."""
    repo = os.path.abspath(repo)
    if is_diff(fix or ""):
        diff = strip_fences(fix)
        bad = [p for p in _diff_paths(diff) if os.path.isabs(p) or not _inside(repo, p)]
        if bad:
            return _result(False, "diff", False, "path_outside_repo", f"refused paths: {', '.join(bad)}")
        if shutil.which("git") is None:
            return _result(False, "diff", False, "git_unavailable")
        mode, new_source = "diff", None
    else:
        rel = (file or "").strip().replace("\\", "/")
        if not rel or os.path.isabs(rel) or not _inside(repo, rel):
            return _result(False, "", False, "path_outside_repo", f"refused path: {file!r}")
        target = os.path.join(repo, rel)
        if not os.path.isfile(target):
            return _result(False, "", False, "file_not_found", rel)
        with open(target, encoding="utf-8") as fh:
            source = fh.read()
        try:
            new_source, mode = apply_fix_to_source(source, function, fix)
        except FixError as e:
            return _result(False, "", False, e.reason, e.detail)

    with temp_repo(repo, "adaptive_fix_") as work:                # shared helper: keeps symlinks as links
        if mode == "diff":
            ok, msg = _git_apply(work, diff, check=True)
            if not ok:
                return _result(False, "diff", False, "diff_does_not_apply", msg)
            ok, msg = _git_apply(work, diff, check=False)
            if not ok:                                                 # pragma: no cover - --check passed
                return _result(False, "diff", False, "diff_does_not_apply", msg)
        else:
            with open(os.path.join(work, rel), "w", encoding="utf-8") as fh:
                fh.write(new_source)
        res = run_pytest_in(work, test_path, timeout, 40, python, test_cmd)
        out = scrub_paths(res["output"], work)
        reason = "passed" if res["passed"] else ("timeout" if res["timed_out"] else "tests_fail")
        return _result(True, mode, bool(res["passed"]), reason, out)
