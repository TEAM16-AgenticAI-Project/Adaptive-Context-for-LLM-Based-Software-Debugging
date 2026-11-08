"""Tests for the system factory (adaptive_context.bench.systems)."""
from __future__ import annotations

import pytest

from adaptive_context.bench import systems
from adaptive_context.models import DebugResult, Diagnosis, RunStats


class _FakeRetriever:
    def search(self, query, k):
        return []


class _FakeLLM:
    def complete(self, system, user):  # pragma: no cover - not exercised here
        raise AssertionError("not called in construction tests")


def test_static_and_adaptive_build():
    r, llm = _FakeRetriever(), _FakeLLM()
    for name in ("static_k3", "static_k10", "adaptive", "adaptive_graph"):
        sys_obj = systems.make_system(name, r, llm)
        assert hasattr(sys_obj, "run")


def test_static_k3_uses_top_k_and_k10_uses_wide():
    r, llm = _FakeRetriever(), _FakeLLM()
    k3 = systems.make_system("static_k3", r, llm, {"top_k": 3, "wide_k": 10})
    k10 = systems.make_system("static_k10", r, llm, {"top_k": 3, "wide_k": 10})
    assert k3.top_k == 3
    assert k10.top_k == 10


def test_adaptive_graph_enables_prefetch():
    r, llm = _FakeRetriever(), _FakeLLM()
    plain = systems.make_system("adaptive", r, llm, {"prefetch_depth": 2})
    graph = systems.make_system("adaptive_graph", r, llm, {"prefetch_depth": 2})
    assert plain.prefetch_depth == 0
    assert graph.prefetch_depth == 2


def test_unknown_system_rejected():
    with pytest.raises(ValueError):
        systems.make_system("nope", _FakeRetriever(), _FakeLLM())


def test_agent_names_error_cleanly_when_module_absent():
    # The agent module may not exist yet; make_system must raise a clear RuntimeError,
    # not a bare ImportError, so the runner can skip it.
    r, llm = _FakeRetriever(), _FakeLLM()
    try:
        from adaptive_context import agent  # noqa: F401
        have_agent = True
    except ImportError:
        have_agent = False
    if have_agent:
        pytest.skip("agent module is present, so the missing-module path cannot be tested")
    with pytest.raises(RuntimeError):
        systems.make_system("agent", r, llm)


def test_available_systems_lists_the_four_core():
    names = systems.available_systems(include_agent=False)
    assert set(names) == {"static_k3", "static_k10", "adaptive", "adaptive_graph"}


def test_result_row_labels_role_and_reads_shared_fields():
    res = DebugResult(
        Diagnosis(file="a.py", function="f", valid=True),
        RunStats(llm_calls=1, input_tokens=100, output_tokens=50, wall_s=2.0, rounds=1),
        [], [], stop_reason="diagnosed")
    row_base = systems.result_row("static_k3", res)
    row_prop = systems.result_row("adaptive", res)
    assert row_base["role"] == "baseline"
    assert row_prop["role"] == "proposed"
    assert row_base["total_tokens"] == 150
    assert row_base["stop_reason"] == "diagnosed"
    assert row_base["wall_s"] == 2.0
