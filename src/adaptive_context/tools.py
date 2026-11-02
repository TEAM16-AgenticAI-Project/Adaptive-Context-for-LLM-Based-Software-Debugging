"""Agent tools. None of them calls the LLM.

`Tools.execute(name, args)` is the single entry point the LangGraph agent uses for native function calls. It
validates arguments, runs the tool, and returns a ToolResult: chunks for the evidence pool, whether they are pinned
(shown in full), any text evidence (a test traceback), and a one-line summary for the agent's action history.

run_failing_test executes repository code, so it works on a temporary copy with a timeout.
That is not a security sandbox: only run tests from repositories you trust.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

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
    else:
        cmd = [python or sys.executable, "-m", "pytest", "-x", "-q", "--tb=short", "-p", "no:cacheprovider",
               *([test_path] if test_path else [])]
    try:
        p = subprocess.run(cmd, cwd=work, env=_clean_env(work), capture_output=True, text=True, timeout=timeout)
        out, code, timed_out = p.stdout + p.stderr, p.returncode, False
    except subprocess.TimeoutExpired as e:
        raw = e.stdout or ""
        out, code, timed_out = (raw.decode(errors="replace") if isinstance(raw, bytes) else raw), -1, True
    lines = out.strip().splitlines()
    return {"passed": code == 0, "timed_out": timed_out, "returncode": code, "output": "\n".join(lines[-tail_lines:])}


def run_failing_test(repo: str, test_path: str | None = None, timeout: int = 60, tail_lines: int = 40,
                     python: str | None = None, test_cmd: str | None = None) -> dict:
    """Run one pytest target in a temporary copy of `repo`; return the end of the output (the traceback).

    Not a security sandbox: it only protects the original files and bounds run time.
    Only run tests from repositories you trust.
    """
    tmp = tempfile.mkdtemp(prefix="adaptive_ctx_")
    work = os.path.join(tmp, "repo")
    try:
        shutil.copytree(repo, work, ignore=IGNORE)
        res = run_pytest_in(work, test_path, timeout, tail_lines, python, test_cmd)
        res["output"] = res["output"].replace(work + os.sep, "").replace(work, ".")
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    _schema("run_failing_test", "Run the failing test and read its traceback. Only available when a test is configured.",
            {}, []),
]
DIAGNOSE_SCHEMA = _schema(
    "diagnose", "Give the final answer once the evidence in your context supports it.",
    {"file": {"type": "string"}, "function": {"type": "string"},
     "cause": {"type": "string", "description": "one sentence citing evidence from the context"},
     "fix": {"type": "string", "description": "the complete corrected function (def ...), or the single corrected line, or ''"},
     "confidence": {"type": "string", "enum": ["high", "low"]}},
    ["file", "function", "cause", "fix", "confidence"])
TOOL_NAMES = tuple(s["function"]["name"] for s in TOOL_SCHEMAS)


class Tools:
    """The six evidence tools of the agent. None of them calls the LLM."""

    def __init__(self, retriever, repo_root: str | None = None, test_path: str | None = None,
                 python: str | None = None, test_cmd: str | None = None, k: int = 3, max_results: int = 6):
        self.retriever, self.repo_root, self.test_path = retriever, repo_root, test_path
        self.python, self.test_cmd, self.k, self.max_results = python, test_cmd, k, max_results
        self._paths = None

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
            return ToolResult(True, f"test {state}; traceback added to context", text=res["output"])
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
        if name == "get_callees":
            out = [c for ch in found for c in self.retriever.callees(ch)][: self.max_results]
        else:
            out = self.callers(value)[: self.max_results]
        label = "callees" if name == "get_callees" else "callers"
        return ToolResult(True, f"{label}: " + ", ".join(c.id for c in out) if out else f"no {label} found",
                          [(c, 0.0) for c in out])
