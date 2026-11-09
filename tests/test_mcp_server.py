"""Tests for the MCP server wrapper (adaptive_context.mcp_server).

These run offline with the simulator backend; no mcp transport and no API key needed.
They exercise the tool functions directly, which is what the MCP runtime calls.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from adaptive_context import mcp_server


def _tiny_repo(tmp: str) -> str:
    """Write a minimal multi-file Python repo with a bug a few hops deep."""
    with open(os.path.join(tmp, "gateway.py"), "w", encoding="utf-8") as fh:
        fh.write("from relay import route\n\n\ndef checkout(value):\n    return route(value)\n")
    with open(os.path.join(tmp, "relay.py"), "w", encoding="utf-8") as fh:
        fh.write("from kernel import fold\n\n\ndef route(value):\n    return fold(value)\n")
    with open(os.path.join(tmp, "kernel.py"), "w", encoding="utf-8") as fh:
        fh.write("def fold(value):\n    return value + 5\n")
    return tmp


def test_diagnose_bug_returns_expected_shape():
    with tempfile.TemporaryDirectory() as tmp:
        _tiny_repo(tmp)
        out = mcp_server.diagnose_bug(tmp, "checkout returns the wrong amount", backend="sim")
    assert "diagnosis" in out
    for key in ("file", "function", "cause", "suggested_fix", "confidence", "valid"):
        assert key in out["diagnosis"]
    assert "evidence_read" in out
    assert "trace" in out
    assert "stats" in out
    assert "total_tokens" in out["stats"]


def test_localize_fault_returns_location_only():
    with tempfile.TemporaryDirectory() as tmp:
        _tiny_repo(tmp)
        out = mcp_server.localize_fault(tmp, "checkout returns the wrong amount", backend="sim")
    assert set(out.keys()) == {"file", "function", "valid", "evidence_read", "stop_reason"}


def test_static_mode_also_builds():
    with tempfile.TemporaryDirectory() as tmp:
        _tiny_repo(tmp)
        out = mcp_server.diagnose_bug(tmp, "bug", mode="static", backend="sim")
    assert "diagnosis" in out


def test_bad_mode_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        _tiny_repo(tmp)
        with pytest.raises(ValueError):
            mcp_server.diagnose_bug(tmp, "bug", mode="nonsense", backend="sim")


def test_groq_without_key_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        _tiny_repo(tmp)
        old = os.environ.pop("GROQ_API_KEY", None)
        try:
            with pytest.raises(ValueError):
                mcp_server.diagnose_bug(tmp, "bug", backend="groq")
        finally:
            if old is not None:
                os.environ["GROQ_API_KEY"] = old


def test_empty_repo_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError):
            mcp_server.diagnose_bug(tmp, "bug", backend="sim")
