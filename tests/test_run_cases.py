"""Tests for the case runner (adaptive_context.bench.run_cases)."""
from __future__ import annotations

import json

from adaptive_context.bench import run_cases
from adaptive_context.cases import RealCase


def _fake_rows():
    return [
        {"system": "static_k3", "case": "c1", "total_tokens": 100, "llm_calls": 1,
         "wall_s": 1.0, "failures": 0, "file_hit": True, "func_hit": True, "grounded": True},
        {"system": "adaptive", "case": "c1", "total_tokens": 200, "llm_calls": 2,
         "wall_s": 2.0, "failures": 0, "file_hit": True, "func_hit": True, "grounded": True},
        {"system": "static_k3", "case": "c2", "total_tokens": 120, "llm_calls": 1,
         "wall_s": 1.1, "failures": 1, "file_hit": False, "func_hit": False, "grounded": False},
        {"system": "adaptive", "case": "c2", "total_tokens": 220, "llm_calls": 3,
         "wall_s": 2.5, "failures": 0, "file_hit": True, "func_hit": False, "grounded": False},
    ]


def test_summarize_counts_and_means():
    summary = run_cases.summarize(_fake_rows(), ["static_k3", "adaptive"])
    assert summary["n_cases"] == 2
    s = summary["systems"]["static_k3"]
    assert s["role"] == "baseline"
    assert s["func_acc"] == 0.5          # one of two cases hit
    assert s["failures"] == 1
    a = summary["systems"]["adaptive"]
    assert a["role"] == "proposed"
    assert a["file_acc"] == 1.0          # adaptive hit the file on both


def test_to_markdown_has_rows_and_roles():
    summary = run_cases.summarize(_fake_rows(), ["static_k3", "adaptive"])
    md = run_cases.to_markdown(summary, "sim")
    assert "| System | Role |" in md
    assert "static_k3" in md and "adaptive" in md
    assert "baseline" in md and "proposed" in md


def test_write_outputs_creates_three_files(tmp_path):
    rows = _fake_rows()
    summary = run_cases.summarize(rows, ["static_k3", "adaptive"])
    run_cases.write_outputs(rows, summary, "sim", str(tmp_path))
    assert (tmp_path / "results.jsonl").exists()
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "table.md").exists()
    data = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert data["summary"]["n_cases"] == 2
    assert data["args"]["llm"] == "sim"


class _FakeSystem:
    def __init__(self, name):
        self.name = name

    def run(self, bug_report):
        from adaptive_context.models import DebugResult, Diagnosis, RunStats
        return DebugResult(
            Diagnosis(file="kernel.py", function="fold", valid=True),
            RunStats(llm_calls=1, input_tokens=50, output_tokens=20, wall_s=0.5, rounds=1),
            [], [], stop_reason="diagnosed")


def test_run_case_scores_against_gold(tmp_path, monkeypatch):
    # A tiny repo on disk so _read_repo_files and chunk_repo have something real.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "kernel.py").write_text("def fold(value):\n    return value + 5\n", encoding="utf-8")
    case = RealCase(id="c1", dataset="controlled", repo=str(repo),
                    bug_report="wrong amount", gold_files=["kernel.py"],
                    gold_functions=["kernel.py::fold"])

    # Patch make_system so no real LLM or retriever is needed.
    monkeypatch.setattr(run_cases, "make_system", lambda name, r, llm, cfg: _FakeSystem(name))
    rows = run_cases.run_case(case, ["static_k3"], llm=None, cfg={})
    assert len(rows) == 1
    assert rows[0]["case"] == "c1"
    assert rows[0]["file_hit"] is True
    assert rows[0]["func_hit"] is True


def test_comparison_on_the_stakeholder_cases_checks_every_fix(tmp_path):
    """The baseline and agent_verify run on the stakeholder manifest; every system's fix is applied and the case's
    tests run the same way, so the table can compare how often the fix really passes."""
    import os

    import pytest
    pytest.importorskip("langgraph")
    from adaptive_context.cases import load_cases
    from adaptive_context.llm import LLMReply, ToolCall

    fixed = 'def checkout(price, discount):\n    """Return the amount the customer pays."""\n' \
            '    return apply_discount(price, discount)\n'

    class Script:                                         # stands in for the model, same answer for every system
        def complete(self, system, user):
            reply = json.dumps({"action": "diagnose", "file": "payment.py", "function": "checkout",
                                "cause": "discount result dropped", "fix": fixed, "confidence": "high"})
            return LLMReply(reply, 10, 5, 0.0)

        def chat(self, messages, tools=None, tool_choice=None):
            return LLMReply("", 10, 5, 0.0, [ToolCall("diagnose", {"file": "payment.py", "function": "checkout",
                                                                   "cause": "c", "fix": fixed,
                                                                   "confidence": "high"})])

    manifest = os.path.join(os.path.dirname(__file__), "..", "data", "stakeholder", "cases.jsonl")
    [case] = [c for c in load_cases(manifest) if c.id == "payment_bug"]
    rows = run_cases.run_case(case, ["static_k3", "agent_verify"], Script(), {}, agent_llm=Script(),
                              check_fixes=True)
    by = {r["system"]: r for r in rows}
    assert by["static_k3"]["func_hit"] and by["static_k3"]["fix_passed"]
    assert by["agent_verify"]["stop_reason"] == "verified" and by["agent_verify"]["fix_passed"]
    summary = run_cases.summarize(rows, ["static_k3", "agent_verify"])
    assert summary["systems"]["agent_verify"]["fix_rate"] == 1.0
    assert "Fix passes tests" in run_cases.to_markdown(summary, "sim")


def test_a_system_without_fix_code_counts_as_not_passing(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "kernel.py").write_text("def fold(value):\n    return value + 5\n", encoding="utf-8")
    (repo / "test_kernel.py").write_text("from kernel import fold\n\n\ndef test_fold():\n    assert fold(1) == 1\n",
                                         encoding="utf-8")
    case = RealCase(id="c1", dataset="t", repo=str(repo), bug_report="wrong amount", gold_files=["kernel.py"],
                    gold_functions=["kernel.py::fold"], test_cmd="pytest -q test_kernel.py")
    monkeypatch.setattr(run_cases, "make_system", lambda name, r, llm, cfg: _FakeSystem(name))
    [row] = run_cases.run_case(case, ["static_k3"], llm=None, cfg={}, check_fixes=True)
    assert row["func_hit"] and row["fix_passed"] is False and row["fix_reason"] == "no_fix"


def test_comparison_report_merges_runs_into_one_file(tmp_path):
    from adaptive_context.bench import compare
    rows = [dict(r, dataset="real", gold_files=["kernel.py"]) for r in _fake_rows()]
    rows[1].update(fix_passed=True, fix_target_passed=True)            # adaptive fixes c1
    rows[0].update(fix_passed=False, fix_target_passed=False)
    summary = run_cases.summarize(rows, ["static_k3", "adaptive"])
    run_cases.write_outputs(rows, summary, "groq", str(tmp_path / "real"), model="openai/gpt-oss-20b")
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"manifest": "m.jsonl", "retrievers": {"bm25": {
        "n": 32, "hit_at_3": 0.34375, "hit_at_10": 0.59375, "all_at_3": 0.25, "all_at_10": 0.5}}}))
    out = tmp_path / "comparison.md"
    compare.main([str(tmp_path / "real"), "--audit", str(audit), "--out", str(out)])
    text = out.read_text()
    assert "groq (openai/gpt-oss-20b)" in text and "Static Top-3 (stakeholder baseline)" in text
    assert "| Adaptive retrieval loop | proposed | 2 | 100% (2/2) | 50% (1/2) | 100% (1/1) |" in text
    assert "| c1 | 1 | right function | **fix passes** |" in text
    assert "top 3 for 34% of the bugs" in text
