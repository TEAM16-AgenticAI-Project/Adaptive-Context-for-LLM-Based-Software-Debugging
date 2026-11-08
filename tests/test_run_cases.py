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
