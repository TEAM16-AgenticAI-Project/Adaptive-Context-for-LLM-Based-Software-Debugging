"""Agent tools. None of them calls the LLM.

`Tools.execute(name, args)` is the single entry point the LangGraph agent uses for native function calls. It
validates arguments, runs the tool, and returns a ToolResult: chunks for the evidence pool, whether they are pinned
(shown in full), any text evidence (a test traceback), and a one-line summary for the agent's action history.

run_failing_test executes repository code, so it works on a temporary copy with a timeout.
That is not a security sandbox: only run tests from repositories you trust.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

from .logs import short

log = logging.getLogger(__name__)

_CLASS = re.compile(r"^class\s+(\w+)\s*(?:\(([^)]*)\))?\s*:", re.M)
_AUX = re.compile(r"(^|/)(tests?|testing|demos?|examples?|docs?|maint|scripts|benchmarks?)/|(^|/)test_[^/]*$|_test\.py$")
IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__", "node_modules", "*.pyc", ".env",
                                ".pytest_cache", ".chroma")
SECRET_ENV = ("GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def _clean_env(work: str) -> dict:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": work}
    for k in SECRET_ENV:
        env.pop(k, None)                                   # never expose API keys to repo code
    return env


def run_pytest_in(work: str, test_path: str | None = None, timeout: int = 60, tail_lines: int = 40,
                  python: str | None = None, test_cmd: str | None = None) -> dict:
    """Run pytest (or an explicit test command such as BugsInPy's `pytest tests/x.py::test_y`) inside `work`.

    `python` selects the interpreter (real repos often need their own environment, e.g. Python 3.8)."""
    if test_cmd:
        cmd = shlex.split(test_cmd)
        if cmd and cmd[0] in ("pytest", "py.test"):
            cmd = [python or sys.executable, "-m", "pytest", *cmd[1:]]
        elif cmd and cmd[0] in ("python", "python3"):      # e.g. BugsInPy's `python -m unittest -q test.x.T.t`
            cmd = [python or sys.executable, *cmd[1:]]
    else:
        cmd = [python or sys.executable, "-m", "pytest", "-x", "-q", "--tb=short", "-p", "no:cacheprovider",
               *([test_path] if test_path else [])]
    t0 = time.perf_counter()
    out, code, timed_out = _run_bounded(cmd, work, timeout)
    secs = time.perf_counter() - t0
    if timed_out:
        log.warning("tests TIMED OUT after %.0fs (killed with their subprocesses): %s", secs, short(" ".join(cmd[1:])))
    else:
        log.debug("tests %s in %.1fs: %s", "passed" if code == 0 else f"failed (rc={code})", secs,
                  short(" ".join(cmd[1:])))
    lines = out.strip().splitlines()
    return {"passed": code == 0, "timed_out": timed_out, "returncode": code, "output": "\n".join(lines[-tail_lines:]),
            "seconds": round(secs, 1)}


def _run_bounded(cmd: list, work: str, timeout: float) -> tuple:
    """Run a command with a hard time limit. Test suites start their own subprocesses (servers, forked workers);
    killing only the direct child can leave them holding the output pipes, and reading the output then blocks
    forever. So the command runs in its own process group and the whole group is killed on timeout."""
    p = subprocess.Popen(cmd, cwd=work, env=_clean_env(work), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=(os.name == "posix"))
    _ACTIVE.add(p)
    try:
        out, _ = p.communicate(timeout=timeout)
        return out or "", p.returncode, False
    except subprocess.TimeoutExpired as e:
        _kill_group(p)
        try:
            out, _ = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:                  # a grandchild still holds the pipe: give up on it
            out = e.output or ""
            for stream in (p.stdout,):
                try:
                    stream.close()
                except OSError:
                    pass
        out = out.decode(errors="replace") if isinstance(out, bytes) else (out or "")
        return out + f"\n[timed out after {timeout:.0f}s]", -1, True
    finally:
        _ACTIVE.discard(p)


_ACTIVE: set = set()          # test processes currently running (each in its own process group)


def kill_active_tests() -> None:
    """Kill every running test process group (used when a whole case is stopped on its time limit)."""
    for p in list(_ACTIVE):
        _kill_group(p)


def _kill_group(p) -> None:
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        else:                                               # pragma: no cover
            p.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            p.kill()
        except OSError:
            pass


@contextlib.contextmanager
def temp_repo(repo: str, prefix: str = "adaptive_"):
    """A throwaway copy of `repo` for running or patching its code; yields the copy's path, removed afterwards.

    Skips git metadata, virtualenvs and caches (IGNORE) and keeps symlinks as links, so dangling links in real
    repositories do not break the copy. Every module that needs a scratch copy uses this one helper."""
    tmp = tempfile.mkdtemp(prefix=prefix)
    work = os.path.join(tmp, "repo")
    try:
        shutil.copytree(repo, work, ignore=IGNORE, symlinks=True)
        yield work
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def scrub_paths(output: str, work: str) -> str:
    """Hide the temporary copy's location in test output."""
    return output.replace(work + os.sep, "").replace(work, ".")


def run_failing_test(repo: str, test_path: str | None = None, timeout: int = 60, tail_lines: int = 40,
                     python: str | None = None, test_cmd: str | None = None) -> dict:
    """Run one pytest target in a temporary copy of `repo`; return the end of the output (the traceback).

    Not a security sandbox: it only protects the original files and bounds run time.
    Only run tests from repositories you trust.
    """
    with temp_repo(repo, "adaptive_ctx_") as work:
        res = run_pytest_in(work, test_path, timeout, tail_lines, python, test_cmd)
        res["output"] = scrub_paths(res["output"], work)
        return res


_WARN_START = re.compile(r"^=+ warnings summary =+$")


def failure_excerpt(output: str, max_lines: int = 16) -> str:
    """The informative part of a pytest/unittest failure: drop the warnings summary, keep the `E ...` lines with a
    little context before them, plus the FAILED summary lines. Falls back to the last lines."""
    lines, keep, skipping = output.splitlines(), [], False
    for ln in lines:
        if _WARN_START.match(ln.strip()):
            skipping = True
        elif skipping and ln.startswith("-- Docs:"):
            skipping = False
            continue
        if not skipping:
            keep.append(ln)
    errs = [i for i, ln in enumerate(keep) if ln.startswith("E ")]
    summary = [ln for ln in keep if ln.startswith(("FAILED ", "ERROR ", "FAIL:", "ERROR:"))][:4]
    if errs:
        body = keep[max(0, errs[0] - 4): errs[-1] + 1][: max_lines - len(summary)]
        return "\n".join(body + [ln for ln in summary if ln not in body])
    return "\n".join(keep[-max_lines:])


def with_failure_output(bug_report: str, result: dict) -> str:
    if result["passed"]:
        return bug_report + "\n\n(The given test passed, so it does not reproduce the failure.)"
    return f"{bug_report}\n\nFailing test output:\n{result['output']}"


@dataclass
class ToolResult:
    ok: bool
    summary: str
    chunks: list = field(default_factory=list)   # [(Chunk, score)]
    pin: bool = False
    text: str = ""                               # non-code evidence, e.g. a traceback


def _schema(name: str, description: str, props: dict, required: list) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required}}}


_NAME = {"name": {"type": "string", "description": "function name: 'checkout', 'Cart.total' or 'path.py::name'"}}

TOOL_SCHEMAS = [
    _schema("search_code", "Keyword search over the repository's functions. Use when you do not know a name yet.",
            {"query": {"type": "string", "description": "short search text"}}, ["query"]),
    _schema("grep_symbol", "List every function/method with this exact name (where it is defined).", _NAME, ["name"]),
    _schema("read_function", "Add the full body of a function to your context. Use to verify a suspect.", _NAME, ["name"]),
    _schema("get_callers", "Add the functions that call this function to your context.", _NAME, ["name"]),
    _schema("get_callees", "Add the functions this function calls to your context.", _NAME, ["name"]),
    _schema("get_related", "Add the same method in the base class, sibling classes and subclasses (often in other "
            "files): where a multi-file bug repeats or where an override changes behaviour.", _NAME, ["name"]),
    _schema("run_failing_test", "Run the failing test and read its traceback. Only available when a test is configured.",
            {}, []),
]
_CHANGE = {"type": "object", "properties": {
    "file": {"type": "string"},
    "function": {"type": "string", "description": "'func', 'Cls.method', or '<module>' for an import or alias"},
    "fix": {"type": "string", "description": "the complete new function (def ...); added if it does not exist"},
    "replace": {"type": "string", "description": "optional: exact current lines of that function; fix then "
                "replaces only these lines"}},
    "required": ["file", "function", "fix"]}
DIAGNOSE_SCHEMA = _schema(
    "diagnose", "Give the final answer once the evidence in your context supports it.",
    {"file": {"type": "string"}, "function": {"type": "string"},
     "cause": {"type": "string", "description": "one sentence citing evidence from the context"},
     "fix": {"type": "string", "description": "the complete corrected function (def ...), or the single corrected line, or ''"},
     "confidence": {"type": "string", "enum": ["high", "low"]},
     "replace": {"type": "string", "description": "optional: the exact current lines that fix replaces (for a long "
                 "function, instead of rewriting all of it)"},
     "other_changes": {"type": "array", "items": _CHANGE,
                       "description": "every other function, in any file, that must change too (multi-file bugs)"}},
    ["file", "function", "cause", "fix", "confidence"])
TOOL_NAMES = tuple(s["function"]["name"] for s in TOOL_SCHEMAS)


class Tools:
    """The six evidence tools of the agent. None of them calls the LLM."""

    def __init__(self, retriever, repo_root: str | None = None, test_path: str | None = None,
                 python: str | None = None, test_cmd: str | None = None, k: int = 3, max_results: int = 6):
        self.retriever, self.repo_root, self.test_path = retriever, repo_root, test_path
        self.python, self.test_cmd, self.k, self.max_results = python, test_cmd, k, max_results
        self._paths = None
        self._classes = None

    @property
    def can_run_test(self) -> bool:
        return bool(self.repo_root and (self.test_path or self.test_cmd))

    def schemas(self) -> list:
        return [s for s in TOOL_SCHEMAS if s["function"]["name"] != "run_failing_test" or self.can_run_test]

    # --- plain tools (kept for scripts and tests) ---
    def search_code(self, query: str, k: int = 3, exclude=frozenset()) -> list:
        return [c.id for c, _ in self.retriever.search(query, k, exclude)]

    def grep_symbol(self, name: str) -> list:
        return [c.id for c in self.retriever.lookup(name)]

    def read_function(self, name: str) -> str:
        return "\n\n".join(c.text for c in self.retriever.lookup(name))

    def get_callees(self, name: str) -> list:
        return [c.id for ch in self.retriever.lookup(name) for c in self.retriever.callees(ch)]

    def get_callers(self, name: str) -> list:
        return [c.id for c in self.callers(name)]

    def callers(self, name: str) -> list:
        short = str(name).split("::")[-1].split(".")[-1]
        return [c for c in self.retriever.chunks if short in c.calls and c.short_name != short]

    def classes(self) -> dict:
        """{class name: [(path, [base names])]} from the class headers in module-level chunks."""
        if self._classes is None:
            self._classes = {}
            for c in self.retriever.chunks:
                if c.kind != "function" and c.kind != "method":
                    for m in _CLASS.finditer(c.text):
                        bases = [b.strip().split(".")[-1] for b in (m.group(2) or "").split(",")
                                 if b.strip() and "=" not in b]
                        entry = (c.path, [b for b in bases if b not in ("object", "")])
                        if entry not in self._classes.setdefault(m.group(1), []):
                            self._classes[m.group(1)].append(entry)
        return self._classes

    def related(self, chunk) -> list:
        """[(relation, chunk)]: the same method in base classes, subclasses and sibling classes (sharing a base)."""
        if "." not in chunk.name:
            return []
        cls, meth = chunk.name.rsplit(".", 1)
        index = self.classes()
        mine = [bases for path, bases in index.get(cls, []) if path == chunk.path] or [[]]
        bases = set(mine[0])
        subs = {name for name, defs in index.items() for _, bs in defs if cls in bs}
        sibs = {name for name, defs in index.items() for _, bs in defs if bases & set(bs)} - {cls}
        out, seen = [], {chunk.id}
        for relation, names in (("base", bases), ("subclass", subs), ("sibling", sibs)):
            for c in self.retriever.chunks:
                if c.id not in seen and c.kind in ("function", "method") and "." in c.name:
                    k, m = c.name.rsplit(".", 1)
                    if m == meth and k in names:
                        out.append((relation, c))
                        seen.add(c.id)
        return sorted(out, key=lambda rc: bool(_AUX.search(rc[1].path)))       # library code before tests/demos

    def inheritors(self, chunk) -> list:
        """'Cls (path)' for direct subclasses that inherit this method without overriding it."""
        if "." not in chunk.name:
            return []
        cls, meth = chunk.name.rsplit(".", 1)
        overriding = {c.name.rsplit(".", 1)[0] for c in self.retriever.chunks if c.name.endswith("." + meth)}
        heirs = [(path == chunk.path, path, name) for name, defs in self.classes().items() for path, bs in defs
                 if cls in bs and name not in overriding and not _AUX.search(path)]
        return [f"{name} ({path})" for _, path, name in sorted(heirs)]          # other files first

    def run_failing_test(self, test_path: str | None = None, timeout: int = 60) -> dict:
        return {"ok": True, **run_failing_test(self.repo_root, test_path or self.test_path, timeout,
                                               python=self.python, test_cmd=self.test_cmd)}

    def _repo_path(self, p: str) -> str | None:
        """The repo file a path written by the model refers to: exact match, else a unique suffix match."""
        if self._paths is None:
            self._paths = sorted({c.path for c in self.retriever.chunks})
        if p in self._paths:
            return p
        hits = [q for q in self._paths if q.endswith("/" + p)]
        return hits[0] if len(hits) == 1 else None

    def locate(self, name: str) -> tuple:
        """Split a name into (repo file, qualified name inside it). The file is None when the name has no module
        part ('match', 'Cart.total'). Accepts 'path.py::Qual', pytest ids 'tests/t.py::TestX::test_y[p]',
        'pkg.mod.Qual', 'path.py' and 'pkg.mod' (qualified name '')."""
        name = name.strip().strip("`()").rstrip("/")
        if "::" in name:
            path, _, qual = name.partition("::")
            return self._repo_path(path), re.sub(r"\[.*\]$", "", qual.replace("::", "."))
        if name.endswith(".py"):
            return self._repo_path(name), ""
        parts = name.split(".")
        for i in range(len(parts), 0, -1):                     # longest module prefix first
            mod = "/".join(parts[:i])
            path = self._repo_path(mod + ".py") or self._repo_path(mod + "/__init__.py")
            if path:
                return path, ".".join(parts[i:])
        return None, name

    def resolve(self, name: str) -> list:
        """The function(s) a name refers to. Uses retriever.lookup, but a file or module written in the name is
        respected: 'bash.py::Bash.from_shell' never returns another file's from_shell (it may be inherited)."""
        name = name.strip().strip("`()")
        found = self.retriever.lookup(name)
        path, qual = self.locate(name)
        if path is None:
            return found                                        # bare or Class.method: the retriever decides
        if not qual:
            return []                                           # a whole file: see members()
        exact = [c for c in self.retriever.chunks if c.path == path and c.name == qual]
        if exact:
            return exact
        same = [c for c in found if c.name == qual]              # e.g. re-exported from pkg/__init__.py
        return same if len(same) == 1 else []

    def members(self, name: str) -> list:
        """Functions in a file ('x.py', 'pkg.mod') or methods of a class ('x.py::Cls', 'pkg.mod.Cls', 'Cls')."""
        path, qual = self.locate(name)
        fns = [c for c in self.retriever.chunks if c.kind in ("function", "method")]
        if path is None:
            return [c for c in fns if "." in c.name and c.name.rsplit(".", 1)[0] == qual]
        if not qual:
            return [c for c in fns if c.path == path]
        return [c for c in fns if c.path == path and c.name.startswith(qual + ".")]

    def _not_found(self, value: str) -> str:
        path, qual = self.locate(value)
        short = (qual or value).split(".")[-1]
        same = [c.id for c in self.retriever.lookup(short) if c.name.split(".")[-1] == short]
        where = f" in {path}" if path else ""
        hint = ""
        if same:
            hint = (f" Functions named '{short}' exist in: {', '.join(same[:5])}" + (" ..." if len(same) > 5 else "")
                    + (" (it may be inherited from one of them)." if path and "." in qual else "."))
        return f"no function '{qual or value}'{where}.{hint}"

    # --- function-calling entry point ---
    def execute(self, name: str, args: dict, exclude=frozenset()) -> ToolResult:
        args = args if isinstance(args, dict) else {}
        if name not in TOOL_NAMES:
            return ToolResult(False, f"unknown tool '{name}'. Use one of: {', '.join(TOOL_NAMES)} or diagnose.")
        if name == "run_failing_test":
            if not self.can_run_test:
                return ToolResult(False, "run_failing_test is not available for this case.")
            res = self.run_failing_test()
            state = "timed out" if res["timed_out"] else ("passed" if res["passed"] else "failed")
            return ToolResult(True, f"test {state}; traceback added to context", text=failure_excerpt(res["output"], 30))
        key = "query" if name == "search_code" else "name"
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            return ToolResult(False, f"{name} needs a non-empty string argument '{key}'.")
        value = value.strip()
        if name == "search_code":
            hits = self.retriever.search(value, self.k, exclude)
            return ToolResult(True, f"{len(hits)} new matches: " + ", ".join(c.id for c, _ in hits) if hits
                              else "no new matches", list(hits))
        found = self.resolve(value)
        inside = [] if found else self.members(value)
        if inside:                                          # a file or a class was passed instead of a function
            kind = "file" if not self.locate(value)[1] else "class"
            if name == "read_function" and len(inside) <= self.max_results:
                found = inside
            else:
                names = ", ".join(c.name for c in inside[:12]) + (" ..." if len(inside) > 12 else "")
                return ToolResult(True, f"'{value}' is a {kind} with {len(inside)} functions: {names}. Call "
                                  f"{name} with one of them as '{inside[0].path}::<name>'.")
        if not found:
            return ToolResult(True, self._not_found(value))
        if name == "grep_symbol":
            more = f" (+{len(found) - 10} more)" if len(found) > 10 else ""
            return ToolResult(True, "defined at " + ", ".join(c.id for c in found[:10]) + more)
        if len(found) > self.max_results:
            # Ambiguous short name (e.g. 136 functions called `match`): adding a few arbitrary ones would waste
            # budget and mislead. Ask for the module-qualified name instead.
            files = sorted({c.path for c in found})
            return ToolResult(True, f"ambiguous: {len(found)} functions named '{value}' in {len(files)} files "
                              f"(e.g. {', '.join(c.id for c in found[:3])}). Call again with the module path, "
                              f"e.g. 'pkg.module.{value.split('.')[-1]}' as imported by the test, or 'path.py::name'.")
        if name == "read_function":
            return ToolResult(True, "added " + ", ".join(c.id for c in found), [(c, 0.0) for c in found], pin=True)
        if name == "get_related":
            rel = [rc for ch in found for rc in self.related(ch)][: self.max_results]
            heirs = [h for ch in found for h in self.inheritors(ch)][:5]
            inherit = f" Inherited without override by: {', '.join(heirs)}." if heirs else ""
            if not rel:
                return ToolResult(True, f"no base, subclass or sibling implementation of '{value}' found.{inherit}")
            return ToolResult(True, "related: " + ", ".join(f"{c.id} ({r})" for r, c in rel) + "." + inherit,
                              [(c, 0.0) for _, c in rel], pin=True)
        if name == "get_callees":
            out = [c for ch in found for c in self.retriever.callees(ch)][: self.max_results]
        else:
            out = self.callers(value)[: self.max_results]
        label = "callees" if name == "get_callees" else "callers"
        return ToolResult(True, f"{label}: " + ", ".join(c.id for c in out) if out else f"no {label} found",
                          [(c, 0.0) for c in out])
