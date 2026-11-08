"""Render benchmark results into Milestone 2 report tables.

Reads a bench summary (either a summary.json produced by bench.run, or a summary
dict passed in process) and writes a Markdown table file plus the summary JSON into
a results directory (default results/m2/). The Markdown tables are formatted to drop
straight into Milestone 2 Section 3 (preliminary evaluation results).

Usage:
    python -m adaptive_context.bench.report --summary results/summary.json --out results/m2
    python -m adaptive_context.bench.report --run --n 50 --out results/m2
"""
from __future__ import annotations

import argparse
import json
import os

SYSTEM_LABELS = {
    "static_k3": "Static RAG (Top-K 3)",
    "static_wide": "Static RAG (wide, Top-K 10)",
    "adaptive": "Adaptive (no graph prefetch)",
    "adaptive_graph": "Adaptive (graph prefetch)",
}

# The two static configurations are the baselines; the proposed system is adaptive.
BASELINES = ("static_k3", "static_wide")
PROPOSED = ("adaptive", "adaptive_graph")


def _fmt(value, spec="{:.3f}"):
    """Format a value, rendering None as a dash so partial runs stay readable."""
    if value is None:
        return "-"
    return spec.format(value)


def main_table(summary: dict) -> str:
    """Build the per-system results table as GitHub-flavored Markdown."""
    systems = summary.get("systems", {})
    lines = []
    lines.append(f"Cases evaluated: {summary.get('n_cases', 0)}  "
                 f"(average chunks per repo: {summary.get('avg_chunks', 0):.0f})")
    lines.append("")
    header = ("| System | Role | Func accuracy | 95% CI | File acc | Grounded | "
              "Fix ok | Tokens | LLM calls | Retrievals | Latency (s) | Failures |")
    sep = "| " + " | ".join(["---"] * 12) + " |"
    lines.append(header)
    lines.append(sep)
    for key, label in SYSTEM_LABELS.items():
        m = systems.get(key)
        if m is None:
            continue
        role = "baseline" if key in BASELINES else "proposed"
        ci = m.get("func_ci95") or [None, None]
        ci_text = "-" if ci[0] is None else f"[{ci[0]:.3f}, {ci[1]:.3f}]"
        lines.append(
            f"| {label} | {role} | {_fmt(m.get('func_acc'))} | {ci_text} | "
            f"{_fmt(m.get('file_acc'))} | {_fmt(m.get('grounded'))} | {_fmt(m.get('fix_ok'))} | "
            f"{_fmt(m.get('tokens'), '{:.0f}')} | {_fmt(m.get('calls'), '{:.2f}')} | "
            f"{_fmt(m.get('retrievals'), '{:.2f}')} | {_fmt(m.get('latency_s'), '{:.2f}')} | "
            f"{m.get('failures', 0)} |")
    return "\n".join(lines)


def depth_table(summary: dict) -> str:
    """Build the accuracy-by-bug-depth table as Markdown."""
    by_depth = summary.get("by_depth", {})
    systems = [s for s in SYSTEM_LABELS if s in summary.get("systems", {})]
    if not by_depth:
        return ""
    lines = ["Function-level accuracy by bug depth (hops from the entry point):", ""]
    header = "| Depth | Cases | " + " | ".join(SYSTEM_LABELS[s] for s in systems) + " |"
    sep = "| " + " | ".join(["---"] * (2 + len(systems))) + " |"
    lines.append(header)
    lines.append(sep)
    for depth in sorted(by_depth, key=lambda x: int(x)):
        row = by_depth[depth]
        cells = " | ".join(_fmt(row.get(s)) for s in systems)
        lines.append(f"| {depth} | {row.get('n', 0)} | {cells} |")
    return "\n".join(lines)


def paired_table(summary: dict) -> str:
    """Build the paired win/loss comparison of the proposed system vs the baseline."""
    paired = summary.get("paired_adaptive_graph_vs_static_k3")
    if not paired:
        return ""
    lines = ["Paired comparison, adaptive (graph prefetch) vs Static RAG (Top-K 3), same cases:", ""]
    lines.append("| Outcome | Cases |")
    lines.append("| --- | --- |")
    lines.append(f"| Both solved | {paired.get('both', 0)} |")
    lines.append(f"| Only adaptive solved | {paired.get('only_adaptive', 0)} |")
    lines.append(f"| Only static solved | {paired.get('only_static', 0)} |")
    lines.append(f"| Neither solved | {paired.get('neither', 0)} |")
    return "\n".join(lines)


def render_markdown(summary: dict, args_note: dict | None = None) -> str:
    """Assemble the full Milestone 2 Section 3 results document."""
    parts = ["# Milestone 2 Preliminary Evaluation Results", ""]
    if args_note:
        llm = args_note.get("llm", "unknown")
        parts.append(f"Run configuration: LLM backend `{llm}`, "
                     f"cases {args_note.get('n', '?')}, seed {args_note.get('seed', '?')}, "
                     f"token budget {args_note.get('budget', '?')}, adaptive rounds "
                     f"{args_note.get('rounds', '?')}.")
        parts.append("")
    parts.append("## System vs baselines")
    parts.append("")
    parts.append(main_table(summary))
    parts.append("")
    depth = depth_table(summary)
    if depth:
        parts.append("## Accuracy by bug depth")
        parts.append("")
        parts.append(depth)
        parts.append("")
    paired = paired_table(summary)
    if paired:
        parts.append("## Paired win and loss analysis")
        parts.append("")
        parts.append(paired)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def write_report(summary: dict, out_dir: str, args_note: dict | None = None) -> tuple[str, str]:
    """Write table.md and summary.json into out_dir and return their paths."""
    os.makedirs(out_dir, exist_ok=True)
    table_path = os.path.join(out_dir, "table.md")
    summary_path = os.path.join(out_dir, "summary.json")
    with open(table_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(summary, args_note))
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump({"args": args_note or {}, "summary": summary}, fh, indent=2)
    return table_path, summary_path


def _load_summary(path: str) -> tuple[dict, dict]:
    """Load a summary.json written by bench.run, tolerating both shapes."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if "summary" in data:
        return data["summary"], data.get("args", {})
    return data, {}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Render benchmark results into Milestone 2 report tables (table.md + summary.json)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--summary", help="path to an existing summary.json from bench.run")
    src.add_argument("--run", action="store_true", help="run the benchmark now, then render")
    ap.add_argument("--out", default=os.path.join("results", "m2"),
                    help="output directory for table.md and summary.json (default results/m2)")
    # Pass-through knobs for --run mode, kept aligned with bench.run defaults.
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--llm", choices=["sim", "groq"], default="sim")
    ap.add_argument("--budget", type=int, default=3000)
    ap.add_argument("--rounds", type=int, default=6)
    a = ap.parse_args(argv)

    if a.run:
        from .run import GenParams, _work, summarize
        gp = GenParams(40, 30, 4)
        cfg = {"top_k": 3, "wide_k": 10, "budget": a.budget, "rounds": a.rounds,
               "llm": a.llm, "model": None, "miss": 0.0}
        rows = [_work((a.seed + i, gp, cfg)) for i in range(a.n)]
        summary = summarize(rows)
        args_note = {"n": a.n, "seed": a.seed, "llm": a.llm, "budget": a.budget, "rounds": a.rounds}
    else:
        summary, args_note = _load_summary(a.summary)

    table_path, summary_path = write_report(summary, a.out, args_note)
    print(f"wrote {table_path}")
    print(f"wrote {summary_path}")


if __name__ == "__main__":
    main()
