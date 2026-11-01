"""Tests for the Milestone 2 report renderer (adaptive_context.bench.report)."""
from __future__ import annotations

import json

from adaptive_context.bench import report


SAMPLE_SUMMARY = {
    "n_cases": 10,
    "avg_chunks": 42.0,
    "systems": {
        "static_k3": {"func_acc": 0.5, "func_ci95": [0.3, 0.7], "file_acc": 0.6,
                      "grounded": 0.4, "evidence_recall": 0.5, "fix_ok": 0.4,
                      "tokens": 800.0, "calls": 1.0, "retrievals": 1.0,
                      "latency_s": 2.0, "failures": 0, "tokens_per_solved": 1600.0},
        "static_wide": {"func_acc": 0.6, "func_ci95": [0.4, 0.8], "file_acc": 0.7,
                        "grounded": 0.5, "evidence_recall": 0.6, "fix_ok": 0.5,
                        "tokens": 1500.0, "calls": 1.0, "retrievals": 1.0,
                        "latency_s": 2.5, "failures": 0, "tokens_per_solved": 2500.0},
        "adaptive": {"func_acc": 0.7, "func_ci95": [0.5, 0.9], "file_acc": 0.8,
                     "grounded": 0.6, "evidence_recall": 0.7, "fix_ok": 0.6,
                     "tokens": 1200.0, "calls": 2.5, "retrievals": 2.5,
                     "latency_s": 4.0, "failures": 0, "tokens_per_solved": 1700.0},
        "adaptive_graph": {"func_acc": 0.8, "func_ci95": [0.6, 0.95], "file_acc": 0.85,
                           "grounded": 0.7, "evidence_recall": 0.8, "fix_ok": 0.7,
                           "tokens": 1300.0, "calls": 2.8, "retrievals": 2.8,
                           "latency_s": 4.5, "failures": 0, "tokens_per_solved": 1625.0},
    },
    "by_depth": {
        "1": {"n": 5, "static_k3": 0.6, "static_wide": 0.7, "adaptive": 0.8, "adaptive_graph": 0.9},
        "2": {"n": 5, "static_k3": 0.4, "static_wide": 0.5, "adaptive": 0.6, "adaptive_graph": 0.7},
    },
    "paired_adaptive_graph_vs_static_k3": {
        "both": 4, "only_adaptive": 3, "only_static": 1, "neither": 2},
}


def test_main_table_marks_baselines_and_proposed():
    md = report.main_table(SAMPLE_SUMMARY)
    assert "Static RAG (Top-K 3)" in md
    assert "baseline" in md
    assert "proposed" in md
    # a Markdown table header and separator are present
    assert "| System |" in md
    assert "| --- |" in md


def test_render_markdown_has_all_sections():
    md = report.render_markdown(SAMPLE_SUMMARY, {"llm": "sim", "n": 10, "seed": 0})
    assert "# Milestone 2 Preliminary Evaluation Results" in md
    assert "## System vs baselines" in md
    assert "## Accuracy by bug depth" in md
    assert "## Paired win and loss analysis" in md


def test_none_values_render_as_dash():
    summary = {"n_cases": 1, "avg_chunks": 1.0,
               "systems": {"static_k3": {"func_acc": None, "func_ci95": None}}}
    md = report.main_table(summary)
    assert "-" in md  # a missing metric does not crash and shows a dash


def test_write_report_creates_files(tmp_path):
    out = tmp_path / "m2"
    table_path, summary_path = report.write_report(
        SAMPLE_SUMMARY, str(out), {"llm": "sim", "n": 10})
    assert (out / "table.md").exists()
    assert (out / "summary.json").exists()
    # the summary file round-trips to JSON
    with open(summary_path, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["summary"]["n_cases"] == 10
    # the table file holds the rendered markdown
    assert "Preliminary Evaluation Results" in (out / "table.md").read_text(encoding="utf-8")
