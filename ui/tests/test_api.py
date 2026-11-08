"""Tests for the web service. Offline: the simulator needs no key and no network.

Run from the repository root:  pytest ui/tests -q
"""
import importlib
import io
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


def _load(monkeypatch, **env):
    for k in ("GROQ_API_KEY", "ALLOW_LOCAL_PATH", "ALLOW_GIT_CLONE", "ALLOW_RUN_TESTS", "RATE_LIMIT_PER_MIN",
              "DEFAULT_LLM"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import main
    return importlib.reload(main)


@pytest.fixture
def client(monkeypatch):
    return TestClient(_load(monkeypatch).app)


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_health_and_config(client):
    assert client.get("/api/health").json()["status"] == "ok"
    cfg = client.get("/api/config").json()
    llms = {item["id"]: item["available"] for item in cfg["llms"]}
    assert llms == {"sim": True, "groq": False}            # no key in the test environment
    assert cfg["default_llm"] == "sim"
    assert cfg["sources"]["local"] is False and cfg["sources"]["example"] is True
    assert {s["id"] for s in cfg["systems"]} >= {"static_k3", "static_k10", "adaptive", "adaptive_graph", "agent"}


def test_examples_listed_and_downloadable(client):
    ids = [e["id"] for e in client.get("/api/examples").json()]
    assert {"payment_bug", "cart_app", "controlled-01"} <= set(ids)
    r = client.get("/api/examples/cart_app/download")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "cart_app/tax.py" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()


def test_bundled_example_works_with_server_paths_disabled(client):
    r = client.post("/api/debug", data={"example": "payment_bug", "systems": "static_k3,adaptive_graph", "llm": "sim"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"]["functions"] == ["payment.py::checkout"]
    assert len(body["runs"]) == 2
    ids = [e["chunk_id"] for e in body["runs"][1]["evidence"]]
    assert "payment.py::checkout" in ids and "discount.py::apply_discount" in ids
    assert body["runs"][1]["evidence"][0]["text"]          # the code the model saw is returned


def test_generated_case_adaptive_beats_static(client):
    r = client.post("/api/debug", data={"gen_seed": "0", "gen_size": "small",
                                        "systems": "static_k3,adaptive_graph", "llm": "sim"})
    assert r.status_code == 200, r.text
    static, adaptive = r.json()["runs"]
    assert static["score"]["func_hit"] is False
    assert adaptive["score"]["func_hit"] and adaptive["score"]["grounded"] and adaptive["score"]["fix_ok"]
    assert any(e["is_answer"] for e in adaptive["evidence"])


def test_agent_system_runs(client):
    pytest.importorskip("langgraph")
    r = client.post("/api/debug", data={"gen_seed": "0", "gen_size": "small", "systems": "agent", "llm": "sim"})
    assert r.status_code == 200, r.text
    run = r.json()["runs"][0]
    assert run["error"] is None and run["score"]["func_hit"] and run["stats"]["tool_calls"] >= 1


def test_upload_zip(client):
    data = _zip({"proj/a.py": "def f(x):\n    return g(x)\n", "proj/b.py": "def g(x):\n    return x - 1\n"})
    r = client.post("/api/debug", data={"bug_report": "f(1) returns 0, expected 2", "systems": "adaptive", "llm": "sim"},
                    files={"upload": ("proj.zip", data, "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["repo"]["files"] == 2


def test_upload_rejects_zip_slip(client):
    data = _zip({"../evil.py": "x = 1\n"})
    r = client.post("/api/debug", data={"bug_report": "x", "llm": "sim"},
                    files={"upload": ("evil.zip", data, "application/zip")})
    assert r.status_code == 400


def test_upload_rejects_non_zip(client):
    r = client.post("/api/debug", data={"bug_report": "x", "llm": "sim"},
                    files={"upload": ("a.zip", b"not a zip", "application/zip")})
    assert r.status_code == 400


def test_git_url_normalization(monkeypatch):
    main = _load(monkeypatch)
    n = main.normalize_git_url
    assert n("https://github.com/owner/repo") == ("https://github.com/owner/repo.git", None)
    assert n("https://github.com/owner/repo.git/") == ("https://github.com/owner/repo.git", None)
    assert n("https://github.com/owner/repo/tree/dev") == ("https://github.com/owner/repo.git", "dev")
    for bad in ("http://github.com/o/r", "https://evil.example.com/o/r", "https://github.com/o",
                "https://user:pw@github.com/o/r", "file:///etc"):
        with pytest.raises(main.HTTPException):
            n(bad)


def test_server_path_disabled_by_default(client):
    r = client.post("/api/debug", data={"local_path": "/tmp", "bug_report": "x", "llm": "sim"})
    assert r.status_code == 403


def test_server_path_when_enabled(monkeypatch, tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    c = TestClient(_load(monkeypatch, ALLOW_LOCAL_PATH="1").app)
    r = c.post("/api/debug", data={"local_path": str(tmp_path), "bug_report": "f returns 1", "llm": "sim"})
    assert r.status_code == 200, r.text


def test_groq_needs_a_key(client):
    r = client.post("/api/debug", data={"example": "payment_bug", "llm": "groq"})
    assert r.status_code == 503


def test_input_validation(client):
    assert client.post("/api/debug", data={"llm": "sim"}).status_code == 400                       # no source
    assert client.post("/api/debug", data={"example": "nope", "llm": "sim"}).status_code == 404
    assert client.post("/api/debug", data={"example": "payment_bug", "systems": "bogus"}).status_code == 400
    assert client.post("/api/debug", data={"example": "payment_bug", "budget": "10"}).status_code == 400
    assert client.post("/api/debug", data={"example": "payment_bug", "test_path": "../x"}).status_code == 400
    two = {"example": "payment_bug", "gen_seed": "1", "llm": "sim"}
    assert client.post("/api/debug", data=two).status_code == 400


def test_legacy_mode_field_still_works(client):
    r = client.post("/api/debug", data={"example": "payment_bug", "mode": "baseline", "llm": "sim"})
    assert r.status_code == 200 and r.json()["runs"][0]["system"] == "static_k3"


def test_rate_limit(monkeypatch):
    c = TestClient(_load(monkeypatch, RATE_LIMIT_PER_MIN="2").app)
    data = {"example": "payment_bug", "systems": "static_k3", "llm": "sim"}
    assert [c.post("/api/debug", data=data).status_code for _ in range(3)] == [200, 200, 429]


def test_fix_verification_disabled_by_default(client):
    import main
    repo = {"case": None, "dir": Path("."), "test_cmd": "pytest -q", "gold": None}
    out = main._verify(repo, _diag("payment.py", "checkout", "return 1"), None, True)
    assert out["skipped"] and "disabled" in out["reason"]


def test_fix_verification_runs_the_test_when_enabled(monkeypatch):
    main = _load(monkeypatch, ALLOW_RUN_TESTS="1")
    ex = main.EXAMPLES["payment_bug"]
    repo = {"case": None, "dir": ex["dir"], "test_cmd": ex["test_cmd"], "gold": ex["gold"]}
    good = main._verify(repo, _diag("payment.py", "checkout", "return apply_discount(price, discount)"), None, True)
    bad = main._verify(repo, _diag("payment.py", "checkout", "return price + 1"), None, True)
    assert good["passed"] is True and bad["passed"] is False


def test_frontend_is_served_with_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "Adaptive-Context Debugger" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/app.js").status_code == 200


def _diag(file, function, fix):
    from adaptive_context.models import Diagnosis
    return Diagnosis(file=file, function=function, cause="c", fix=fix, confidence="high", valid=True)
