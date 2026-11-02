"""Smoke tests for the debugger API. Run: cd backend && python -m pytest ../tests -q

These use the offline SimulatedLLM so they need no API key and no network.
"""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
from main import app  # noqa: E402

client = TestClient(app)
EXAMPLE = str((Path(__file__).parent.parent / "examples" / "payment_bug").resolve())


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_example_endpoint():
    r = client.get("/api/example")
    assert r.status_code == 200 and r.json()["bug_report"]


def test_debug_adaptive_on_example():
    r = client.post("/api/debug", data={
        "bug_report": "checkout(100, 0.20) returns 100 instead of 80.",
        "mode": "adaptive", "llm": "sim", "local_path": EXAMPLE, "budget": "3000",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # the engine should retrieve the relevant code as evidence
    ids = [c["chunk_id"] for c in body["context"]]
    assert any("checkout" in i for i in ids)
    assert body["stop_reason"]
    assert body["repo_stats"]["files"] >= 2


def test_debug_requires_one_source():
    r = client.post("/api/debug", data={"bug_report": "x", "llm": "sim"})
    assert r.status_code == 400


def test_debug_rejects_bad_mode():
    r = client.post("/api/debug", data={"bug_report": "x", "mode": "nope", "local_path": EXAMPLE})
    assert r.status_code == 400
