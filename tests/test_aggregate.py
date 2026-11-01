"""Tests for the results aggregation and validation module."""
from __future__ import annotations

import json

from adaptive_context.bench import aggregate


def _write_summary(path, backend, n_cases=10):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "args": {"llm": backend, "n": n_cases},
        "summary": {"n_cases": n_cases, "systems": {"static_k3": {"func_acc": 0.5}}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_classify_backend():
    assert aggregate.classify_backend("sim") == "simulator"
    assert aggregate.classify_backend("groq") == "live"
    assert aggregate.classify_backend("something-else") == "unknown"


def test_load_results_finds_nested_summaries(tmp_path):
    _write_summary(tmp_path / "sim_run" / "summary.json", "sim")
    _write_summary(tmp_path / "live_run" / "summary.json", "groq")
    records = aggregate.load_results(str(tmp_path))
    assert len(records) == 2
    backends = {r["backend"] for r in records}
    assert backends == {"sim", "groq"}


def test_validate_flags_mixed_backends(tmp_path):
    _write_summary(tmp_path / "a" / "summary.json", "sim")
    _write_summary(tmp_path / "b" / "summary.json", "groq")
    records = aggregate.load_results(str(tmp_path))
    problems = aggregate.validate(records)
    assert any("mix simulator and live" in p for p in problems)


def test_validate_clean_when_all_simulator(tmp_path):
    _write_summary(tmp_path / "a" / "summary.json", "sim")
    _write_summary(tmp_path / "b" / "summary.json", "sim")
    records = aggregate.load_results(str(tmp_path))
    assert aggregate.validate(records) == []


def test_validate_flags_empty_run(tmp_path):
    _write_summary(tmp_path / "a" / "summary.json", "sim", n_cases=0)
    records = aggregate.load_results(str(tmp_path))
    problems = aggregate.validate(records)
    assert any("n_cases is zero" in p for p in problems)


def test_validate_reports_no_results():
    assert any("no results" in p for p in aggregate.validate([]))


def test_consolidate_groups_and_counts(tmp_path):
    _write_summary(tmp_path / "s1" / "summary.json", "sim")
    _write_summary(tmp_path / "s2" / "summary.json", "sim")
    _write_summary(tmp_path / "l1" / "summary.json", "groq")
    report = aggregate.consolidate(str(tmp_path))
    assert report["n_files"] == 3
    assert len(report["groups"]["simulator"]) == 2
    assert len(report["groups"]["live"]) == 1
    # mixed set, so there is a problem recorded
    assert report["problems"]
