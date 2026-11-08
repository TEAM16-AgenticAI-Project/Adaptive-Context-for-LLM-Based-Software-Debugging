"""Web UI: test-case list, a full agent_verify job on the stakeholder auth_bug, and a two-file fix shown per file."""
import io
import os
import time
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("langgraph")

from fastapi.testclient import TestClient  # noqa: E402

from adaptive_context import cli  # noqa: E402
from adaptive_context.llm import LLMReply, ToolCall  # noqa: E402
from adaptive_context.webui import server  # noqa: E402

FIXED = '''def can_access(user, action):
    """Return whether an active user may perform an action."""
    role = str(user.get("role", "guest")).strip().lower()
    return bool(user.get("active", False)) and action in permissions_for(role)'''


class Script:
    def __init__(self, steps):
        self.steps = list(steps)

    def chat(self, messages, tools=None, tool_choice=None):
        return LLMReply("", 10, 5, 0.0, [self.steps.pop(0) if self.steps else ToolCall("diagnose", {})])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(server.SETTINGS, "envs", {})
    return TestClient(server.create_app())


def _scripted(monkeypatch, steps):
    monkeypatch.setattr(cli, "make_llm", lambda *a, **k: Script(steps))


def _wait(client, job):
    for _ in range(300):
        s = client.get(f"/api/jobs/{job}").json()
        if s["status"] in ("done", "error"):
            return s
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_lists_the_stakeholder_cases_and_the_benchmark(client):
    ex = {e["id"]: e for e in client.get("/api/examples").json()}
    assert ex["example:payment_bug"]["test_cmd"] == "pytest -q test_payment.py"
    assert ex["example:auth_bug"]["ready"] and "permissions.py" in ex["example:auth_bug"]["note"]
    assert "repo" not in ex["example:auth_bug"]                                  # no server paths in the page
    if "case:thefuck-16" in ex:                                                  # listed, but needs its test env
        assert ex["case:thefuck-16"]["multi"] and not ex["case:thefuck-16"]["ready"]
    assert client.get("/").status_code == 200 and "Adaptive-Context Debugger" in client.get("/").text


def test_agent_verify_job_on_auth_bug_shows_the_change_and_the_check(client, monkeypatch):
    _scripted(monkeypatch, [ToolCall("read_function", {"name": "can_access"}),
                            ToolCall("diagnose", {"file": "auth.py", "function": "can_access", "fix": FIXED,
                                                  "cause": "admins skip the active check", "confidence": "high"})])
    r = client.post("/api/debug", data={"bug_report": "deactivated admin can export", "mode": "agent_verify",
                                        "llm": "sim", "example": "example:auth_bug",
                                        "test_cmd": "pytest -q test_auth.py"})
    s = _wait(client, r.json()["job"])
    assert s["status"] == "done", s["error"]
    res = s["result"]
    assert res["stop_reason"] == "verified" and res["verification"]["passed"]
    [f] = res["changes"]["files"]
    assert f["file"] == "auth.py" and "+    return bool(user.get(\"active\", False))" in f["diff"]
    assert any("fix check 1" in line for line in s["log"])                        # the live log reached the job


def test_a_two_file_fix_is_shown_as_two_diffs(client, monkeypatch, tmp_path):
    (tmp_path / "price.py").write_text("def net(p):\n    return p\n")
    (tmp_path / "tax.py").write_text("from price import net\n\n\ndef total(p):\n    return net(p) * 1.0\n")
    (tmp_path / "test_tax.py").write_text("from tax import total\n\n\ndef test_total():\n    assert total(100) == 88.0\n")
    other = [{"file": "tax.py", "function": "total", "fix": "def total(p):\n    return net(p) * 1.1\n"}]
    _scripted(monkeypatch, [ToolCall("diagnose", {"file": "price.py", "function": "net",
                                                  "fix": "def net(p):\n    return p * 0.8\n",
                                                  "cause": "discount and tax missing", "confidence": "high",
                                                  "other_changes": other})])
    r = client.post("/api/debug", data={"bug_report": "total(100) should be 88", "mode": "agent_verify", "llm": "sim",
                                        "local_path": str(tmp_path), "test_cmd": "pytest -q test_tax.py"})
    s = _wait(client, r.json()["job"])
    assert s["status"] == "done", s["error"]
    files = s["result"]["changes"]["files"]
    assert [f["file"] for f in files] == ["price.py", "tax.py"] and files[1]["places"] == ["total"]
    assert s["result"]["verification"]["passed"]
    assert (tmp_path / "price.py").read_text() == "def net(p):\n    return p\n"   # the preview never edits the repo


def test_uploads_are_checked_and_their_tests_not_run(client, monkeypatch):
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.py", "x = 1\n")
    r = client.post("/api/debug", data={"bug_report": "x", "mode": "agent", "llm": "sim"},
                    files={"upload": ("r.zip", bad.getvalue(), "application/zip")})
    assert "unsafe path" in _wait(client, r.json()["job"])["error"]
    good = io.BytesIO()
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("app/m.py", "def f():\n    return 1\n")
        zf.writestr("app/test_m.py", "def test_f():\n    assert False\n")
    r = client.post("/api/debug", data={"bug_report": "f is wrong", "mode": "agent_verify", "llm": "sim",
                                        "test_cmd": "pytest -q test_m.py"},
                    files={"upload": ("r.zip", good.getvalue(), "application/zip")})
    s = _wait(client, r.json()["job"])
    assert s["status"] == "error" and "needs a test command" in s["error"]       # its tests were not run
    assert not os.path.exists("evil.py")
