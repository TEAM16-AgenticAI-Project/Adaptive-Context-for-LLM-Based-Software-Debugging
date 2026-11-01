"""Generate the Section 7 team-contributions table from the Git history.

Milestone 2 Section 7 requires, for each team member, their name, GitHub username,
a summary of contributions, and links to commits. This reads the repository's Git
log and produces a per-author summary (commit count, files touched, line changes,
and the commit subjects) as Markdown, so Section 7 is grounded in the real history
rather than guessed. Commit counts alone are not a sufficient contribution summary;
this table is a starting point that each member extends with the non-commit work
(datasets, evaluation, stakeholder communication, writing).

Usage:
    python -m adaptive_context.bench.contributions --out results/m2/contributions.md
"""
from __future__ import annotations

import argparse
import os
import subprocess
from collections import defaultdict


def _git(args: list[str], repo: str) -> str:
    """Run a git command in repo and return stdout, or '' on failure."""
    try:
        out = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True, text=True, check=True, timeout=60)
        return out.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def collect(repo: str, ref: str = "HEAD") -> dict:
    """Collect per-author commit and churn statistics from the Git log.

    Returns a mapping author_email -> record with name, commit count, subjects,
    files touched, and inserted/deleted line totals.
    """
    # One line per commit: hash, author name, author email, subject.
    log = _git(["log", ref, "--no-merges", "--pretty=format:%h\x1f%an\x1f%ae\x1f%s"], repo)
    authors: dict[str, dict] = {}
    commit_meta: dict[str, tuple] = {}
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        h, name, email, subject = parts
        rec = authors.setdefault(email, {
            "name": name, "email": email, "commits": 0, "subjects": [],
            "files": set(), "insertions": 0, "deletions": 0})
        rec["commits"] += 1
        rec["subjects"].append((h, subject))
        commit_meta[h] = (email, name)

    # Churn per commit via numstat, attributed back to the commit's author.
    numstat = _git(
        ["log", ref, "--no-merges", "--numstat", "--pretty=format:\x1e%h"], repo)
    current = None
    for line in numstat.splitlines():
        if line.startswith("\x1e"):
            current = line[1:].strip()
            continue
        if current is None or current not in commit_meta:
            continue
        cols = line.split("\t")
        if len(cols) != 3:
            continue
        added, deleted, path = cols
        email = commit_meta[current][0]
        rec = authors.get(email)
        if rec is None:
            continue
        rec["files"].add(path)
        # Binary files show '-' instead of a count.
        if added.isdigit():
            rec["insertions"] += int(added)
        if deleted.isdigit():
            rec["deletions"] += int(deleted)
    return authors


def render(authors: dict, repo_url: str = "") -> str:
    """Render the per-author statistics as a Markdown Section 7 starter table."""
    lines = ["# Section 7: Team Contributions (from Git history)", ""]
    if repo_url:
        lines.append(f"Repository: {repo_url}")
        lines.append("")
    lines.append("Per-author activity from the commit history (no-merge commits). "
                 "Commit counts alone are not a complete contribution summary; each member "
                 "adds their non-commit work (datasets, evaluation, stakeholder communication, "
                 "writing) below their row.")
    lines.append("")
    lines.append("| Author | Email | Commits | Files touched | Lines + | Lines - |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for rec in sorted(authors.values(), key=lambda r: r["commits"], reverse=True):
        lines.append(
            f"| {rec['name']} | {rec['email']} | {rec['commits']} | "
            f"{len(rec['files'])} | {rec['insertions']} | {rec['deletions']} |")
    lines.append("")
    lines.append("## Commit subjects per author")
    lines.append("")
    for rec in sorted(authors.values(), key=lambda r: r["commits"], reverse=True):
        lines.append(f"### {rec['name']}")
        for h, subject in rec["subjects"]:
            lines.append(f"- `{h}` {subject}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Generate the Section 7 contributions table from the Git history")
    ap.add_argument("--repo", default=".", help="path to the git repository (default .)")
    ap.add_argument("--ref", default="HEAD", help="git ref to summarize (default HEAD)")
    ap.add_argument("--repo-url", default="", help="repository URL to record in the header")
    ap.add_argument("--out", default=os.path.join("results", "m2", "contributions.md"),
                    help="output Markdown path")
    a = ap.parse_args(argv)

    authors = collect(a.repo, a.ref)
    md = render(authors, a.repo_url)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(f"wrote {a.out} ({len(authors)} author(s))")


if __name__ == "__main__":
    main()
