"""Tools that run outside the LLM. run_failing_test executes repo code, so it works on a temp copy with a timeout."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__", "node_modules", "*.pyc", ".env")


def run_failing_test(repo: str, test_path: str | None = None, timeout: int = 60, tail_lines: int = 40) -> dict:
    """Run one pytest target in a temporary copy of `repo`; return the end of the output (the traceback).

    Not a security sandbox: it only protects the original files and bounds run time.
    Only run tests from repositories you trust.
    """
    tmp = tempfile.mkdtemp(prefix="adaptive_ctx_")
    work = os.path.join(tmp, "repo")
    try:
        shutil.copytree(repo, work, ignore=IGNORE)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": work}
        env.pop("GROQ_API_KEY", None)                      # never expose the API key to repo code
        try:
            p = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "--tb=short", *([test_path] if test_path else [])],
                               cwd=work, env=env, capture_output=True, text=True, timeout=timeout)
            out, code, timed_out = p.stdout + p.stderr, p.returncode, False
        except subprocess.TimeoutExpired as e:
            out, code, timed_out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), -1, True
        lines = out.strip().splitlines()
        return {"passed": code == 0, "timed_out": timed_out, "returncode": code, "output": "\n".join(lines[-tail_lines:])}
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
