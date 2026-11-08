"""Tests for the Section 7 contributions generator."""
from __future__ import annotations

from adaptive_context.bench import contributions


SAMPLE_AUTHORS = {
    "kkundur1@users.noreply.github.com": {
        "name": "Karthik Venkata Sai Reddy Kunduru",
        "email": "kkundur1@users.noreply.github.com",
        "commits": 3,
        "subjects": [("a1b2c3d", "Add results reporting"),
                     ("e4f5g6h", "Add aggregation"),
                     ("i7j8k9l", "Add contributions table")],
        "files": {"src/adaptive_context/bench/report.py",
                  "src/adaptive_context/bench/aggregate.py"},
        "insertions": 400,
        "deletions": 10,
    },
    "teammate@example.com": {
        "name": "Teammate",
        "email": "teammate@example.com",
        "commits": 1,
        "subjects": [("m0n1o2p", "Add context manager")],
        "files": {"src/adaptive_context/budget.py"},
        "insertions": 120,
        "deletions": 5,
    },
}


def test_render_has_table_and_rows():
    md = contributions.render(SAMPLE_AUTHORS, repo_url="https://example.com/repo")
    assert "# Section 7: Team Contributions" in md
    assert "https://example.com/repo" in md
    assert "Karthik Venkata Sai Reddy Kunduru" in md
    assert "| Author | Email | Commits |" in md
    # the author with more commits is listed first
    karthik_pos = md.index("Karthik Venkata Sai Reddy Kunduru")
    teammate_pos = md.index("Teammate")
    assert karthik_pos < teammate_pos


def test_render_lists_commit_subjects():
    md = contributions.render(SAMPLE_AUTHORS)
    assert "Add results reporting" in md
    assert "`a1b2c3d`" in md


def test_render_handles_no_authors():
    md = contributions.render({})
    assert "# Section 7: Team Contributions" in md


def test_collect_runs_on_this_repo(tmp_path):
    # collect() against a non-repo path returns an empty mapping, not a crash.
    result = contributions.collect(str(tmp_path))
    assert result == {}
