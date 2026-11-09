"""One comparison file: the baselines and the proposed systems on the same bugs with the same model.

Reads the folders written by `adaptive-context cases` (results.jsonl + summary.json, one row per bug and system)
and writes a Markdown report: an overall table, one table per dataset, a per-bug grid and the definitions. Optional
extras: the LLM-free retrieval audit (how often static Top-K retrieval even shows the buggy function) and single
`adaptive-context debug` runs (e.g. a larger model on one bug), which are listed separately and never mixed into
the tables.

    python -m adaptive_context.bench.compare results/m2/compare/stakeholder results/m2/compare/thefuck \
        --audit results/deep/audit/summary.json --extra "thefuck-16, gpt-oss-120b=results/agent_live/run.json" \
        --out results/m2/compare/comparison.md
"""
from __future__ import annotations

import argparse
import json
import os
import time

ORDER = ("static_k3", "static_k10", "adaptive", "adaptive_graph", "agent", "agent_verify")
LABEL = {"static_k3": "Static Top-3 (stakeholder baseline)", "static_k10": "Static Top-10 (wider baseline)",
         "adaptive": "Adaptive retrieval loop", "adaptive_graph": "Adaptive + call-graph prefetch",
         "agent": "LangGraph agent (tools)", "agent_verify": "Agent + fix check (ours)"}


def load_run(folder: str) -> tuple:
    """(rows, args) from one `adaptive-context cases` output folder."""
    rows = []
    with open(os.path.join(folder, "results.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    args = {}
    path = os.path.join(folder, "summary.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            args = json.load(fh).get("args", {})
    return rows, args


def _systems(rows) -> list:
    seen = {r["system"] for r in rows}
    return [s for s in ORDER if s in seen] + sorted(seen - set(ORDER))


def _pct(xs) -> str:
    return f"{100 * sum(xs) / len(xs):.0f}% ({sum(xs)}/{len(xs)})" if xs else "n/a"


def _mean(xs, fmt="{:.0f}") -> str:
    return fmt.format(sum(xs) / len(xs)) if xs else "n/a"


def table(rows) -> str:
    """Per-system table over the given rows."""
    out = ["| System | Role | Bugs | Right file | Right function | Fix passes tests | Avg tokens | Avg LLM calls |"
           " Avg time (s) |", "| --- " * 9 + "|"]
    for s in _systems(rows):
        rs = [r for r in rows if r["system"] == s]
        fixed = [bool(r["fix_passed"]) for r in rs if "fix_passed" in r]
        role = "baseline" if s.startswith("static") else "proposed"
        out.append(f"| {LABEL.get(s, s)} | {role} | {len(rs)} | {_pct([bool(r['file_hit']) for r in rs])} | "
                   f"{_pct([bool(r['func_hit']) for r in rs])} | {_pct(fixed)} | "
                   f"{_mean([r.get('total_tokens', 0) for r in rs])} | "
                   f"{_mean([r.get('llm_calls', 0) for r in rs], '{:.1f}')} | "
                   f"{_mean([r.get('elapsed_s', r.get('wall_s', 0)) for r in rs], '{:.1f}')} |")
    return "\n".join(out)


def grid(rows) -> str:
    """Bug x system: what each system got on each bug."""
    systems = _systems(rows)
    out = ["| Bug | Files in the fix | " + " | ".join(LABEL.get(s, s).split(" (")[0] for s in systems) + " |",
           "| --- " * (2 + len(systems)) + "|"]
    for case in dict.fromkeys(r["case"] for r in rows):
        cells = []
        for s in systems:
            r = next((x for x in rows if x["case"] == case and x["system"] == s), None)
            if r is None:
                cells.append("not run")
            elif r.get("fix_passed"):
                cells.append("**fix passes**")
            elif r.get("func_hit"):
                cells.append("right function")
            elif r.get("file_hit"):
                cells.append("right file")
            else:
                cells.append("missed")
        files = next((len(x.get("gold_files") or []) for x in rows if x["case"] == case and x.get("gold_files")), 0)
        out.append(f"| {case} | {files or '?'} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def audit_section(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        a = json.load(fh)
    b = a.get("retrievers", {}).get("bm25", {})
    if not b:
        return ""
    return ("## Why one retrieval is not enough (LLM-free audit)\n\n"
            f"On all {b.get('n', '?')} real bugs of `{a.get('manifest', 'the manifest')}`, static BM25 retrieval puts "
            f"a buggy function in the top 3 for {100 * b['hit_at_3']:.0f}% of the bugs and in the top 10 for "
            f"{100 * b['hit_at_10']:.0f}%; every buggy function is in the top 3 for {100 * b['all_at_3']:.0f}% "
            f"(top 10: {100 * b['all_at_10']:.0f}%). A single-shot Top-K baseline cannot fix code it never sees.\n")


def extra_section(extras: list) -> str:
    lines = ["## Single runs reported separately (not in the tables)\n",
             "| Run | Result | Files changed | LLM calls | Tokens | Time (s) |", "| --- " * 6 + "|"]
    for label, path in extras:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        v, s = d.get("verification") or {}, d.get("stats") or {}
        lines.append(f"| {label} | {d.get('stop_reason', '?')} | {', '.join(v.get('files_changed') or []) or '-'} | "
                     f"{s.get('llm_calls', '?')} | {s.get('total_tokens', '?')} | {s.get('wall_s', 0):.0f} |")
    return "\n".join(lines) + "\n"


DEFINITIONS = """## How to read this

- **Same conditions for every system:** the same model, the same repository index (BM25 over function-level chunks)
  and the same bug report. Evaluator-only gold labels (the maintainer's fix locations) are used for scoring and are
  never shown to a system.
- **Right file / Right function:** the diagnosed location matches a file / function changed by the maintainer's fix.
- **Fix passes tests:** the system's final fix (every place it changes, in any file) is applied to a temporary copy,
  then the bug's failing test and the related test files are run. It passes only if the failing test passes and no
  related test breaks. The same check runs for the baselines, which get no feedback from it; `agent_verify` uses it
  inside its loop and may repair its fix up to the configured number of times.
- **Tokens / LLM calls / time:** averages per bug, input plus output tokens.
"""


def build(run_dirs, audit=None, extras=()) -> str:
    rows, backends = [], set()
    for folder in run_dirs:
        rs, args = load_run(folder)
        for r in rs:
            r.setdefault("dataset", os.path.basename(os.path.normpath(folder)))
        rows += rs
        backends.add(f"{args.get('llm', '?')}" + (f" ({args['model']})" if args.get("model") else ""))
    parts = [f"# Baseline vs proposed: comparison\n\nGenerated {time.strftime('%Y-%m-%d %H:%M')} from "
             f"{', '.join(f'`{d}`' for d in run_dirs)}. Backend: {', '.join(sorted(backends))}. "
             f"Bugs: {len({r['case'] for r in rows})}.\n",
             "## All bugs\n\n" + table(rows) + "\n"]
    datasets = list(dict.fromkeys(r["dataset"] for r in rows))
    if len(datasets) > 1:
        for ds in datasets:
            parts.append(f"## {ds}\n\n" + table([r for r in rows if r["dataset"] == ds]) + "\n")
    parts.append("## Per bug\n\n" + grid(rows) + "\n")
    if audit and os.path.exists(audit):
        parts.append(audit_section(audit))
    if extras:
        parts.append(extra_section(extras))
    parts.append(DEFINITIONS)
    return "\n".join(p for p in parts if p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="merge `adaptive-context cases` outputs into one comparison file")
    ap.add_argument("runs", nargs="+", help="output folders of `adaptive-context cases`")
    ap.add_argument("--audit", default=None, help="retrieval audit summary.json (optional)")
    ap.add_argument("--extra", action="append", default=[],
                    help="LABEL=PATH of an `adaptive-context debug` JSON to list separately (repeatable)")
    ap.add_argument("--out", default=os.path.join("results", "m2", "comparison.md"))
    a = ap.parse_args(argv)
    extras = [tuple(e.rsplit("=", 1)) for e in a.extra if "=" in e and os.path.exists(e.rsplit("=", 1)[1])]
    text = build([r for r in a.runs if os.path.exists(os.path.join(r, "results.jsonl"))], a.audit, extras)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
