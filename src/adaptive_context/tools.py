"""Tools that run outside the LLM. run_failing_test executes repo code, so it works on a temp copy with a timeout."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile

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


class Tools:
    """The six tools of the agent. None of them calls the LLM."""

    def __init__(self, retriever, repo_root: str, test_path: str | None = None):
        self.retriever, self.repo_root, self.test_path = retriever, repo_root, test_path

    def search_code(self, query: str, k: int = 3, exclude=frozenset()) -> list:
        return [c.id for c, _ in self.retriever.search(query, k, exclude)]

    def grep_symbol(self, name: str) -> list:
        return [c.id for c in self.retriever.lookup(name)]

    def read_function(self, name: str) -> str:
        return "\n\n".join(c.text for c in self.retriever.lookup(name))

    def get_callees(self, name: str) -> list:
        return [c.id for ch in self.retriever.lookup(name) for c in self.retriever.callees(ch)]

    def get_callers(self, name: str) -> list:
        short = name.split(".")[-1]
        return [c.id for c in self.retriever.chunks if short in c.calls and c.short_name != short]

    def run_failing_test(self, test_path: str | None = None, timeout: int = 60) -> dict:
        return {"ok": True, **run_failing_test(self.repo_root, test_path or self.test_path, timeout)}
