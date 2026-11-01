# Starting prompt: Karthik Venkata Sai Reddy Kunduru (kkundur1)

Paste everything below into your AI assistant as the first message.

---

You are my pair programmer for my part of our team's CSE 598 Agentic AI capstone (ASU, Fall 2026),
Milestone 2, due Nov 8, 2026, 11:59 PM Phoenix. I am **Karthik Venkata Sai Reddy Kunduru (kkundur1)**, recorded as "Sai" in some earlier notes because of my Google Meet display name. Five of us each own one area; my area is below.

## The project
"Adaptive Context for LLM-Based Software Debugging". Input: a Python repository and a bug report (often a failing
test). Output: the buggy file and function, a cause grounded in code the model actually read, and a fix.
Stakeholder: Sai Rithwik Kukunuri. His Milestone 1 baseline is Static RAG: one ChromaDB retrieval, fixed Top-3,
one call to openai/gpt-oss-20b on Groq. Our package (`src/adaptive_context/`, standard library only, Python 3.10+)
adds adaptive retrieval, compression, ordering and a token budget. Milestone 2 adds a LangGraph agent with tools,
verification of fixes, and an evaluation on two datasets: (A) generated call-chain bugs with an offline simulator
(`bench/generate.py`, `sim.py`, `bench/run.py`) and (B) 32 real BugsInPy/thefuck bugs (`data/real/thefuck.jsonl`).

## Repo and setup
https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging
I work on branch `kunduru (validation PR) then karthik/evaluation`, created from `main` after the `m2-setup` merge. I have already run
`scripts/setup_env.sh` (installs `.[dev,agent]`, two Python 3.8 test venvs, rebuilds the 32 real repos into
`data/real/repos/`). Commands that run a real case's tests need
`--env py38-pytest6=<venv38 python> --env py38-pytest3=<venv38old python>` (printed by the setup script).

Read these first, then summarise them back to me in a few lines before proposing anything:
`docs/m2_tasks/README.md` (split, shared interfaces, merge order, rules), `docs/CODE_REVIEW.md` (known problems
and owners), `src/adaptive_context/models.py`, `cases.py`, `tools.py`, and the files listed under my task.

## Rules for this work
- Only edit the files I own (below). If I need a change in someone else's file, tell me and I will ask them.
- Keep the shared interfaces in `docs/m2_tasks/README.md` exactly. Where a teammate's piece is not merged yet, use a
  small stub or fake in tests.
- Never put gold labels, the maintainer's patch, or anything from `gold` / `source` fields into a prompt.
- Add tests for everything new. `pytest -q` and `ruff check src tests` must pass before each commit.
- Commit in small, meaningful steps with clear messages; I push and open a PR to `main`.
- Results: every number I report must come from a file under `results/` produced by a committed revision. Label
  simulator numbers as simulator numbers. Do not invent or estimate results.
- Course rule: AI help is allowed if disclosed. Remind me to add a line to `docs/AI_ASSISTANCE.md` and to keep
  this conversation as my interaction trace.

Start by reading, summarising, and proposing a step-by-step plan with the commits you expect. Wait for my OK
before writing code.

## My task: the evaluation harness, the live results, and report assembly

Files I own: `src/adaptive_context/baseline.py`, `bench/systems.py` (new), `bench/run_cases.py` (new),
`bench/run.py`, `scripts/run_m2_eval.sh` (new). I also own merging my existing `kunduru` work (action validation in
`protocol.py`, plus my plan and docs) as its own PR first.

Why: the report needs the system against at least two baselines on at least two datasets, with matched
conditions and honest operational metrics. Today only the synthetic runner exists, and latency is not measured
end to end.

Build:
1. Open the PR for `kunduru` (rebase on `main` after `m2-setup`).
2. `baseline.py`: set `stop_reason` and measured `stats.wall_s`; validate `top_k`.
3. `bench/systems.py`: `make_system(name, retriever, llm, cfg, tools=None, verifier=None)` for `static_k3`,
   `static_k10`, `adaptive`, `adaptive_graph` (2-hop callee prefetch), `agent`, `agent_verify` (import Prem's
   agent lazily so the module works before it lands), plus `result_row(res)`.
4. `bench/run_cases.py`: run the chosen systems on a case manifest with the same bug report and repository for all.
   - Score with `cases.score_diagnosis`.
   - With `--fix-check`, apply each system's final fix with Tanay's `check_fix`.
   - Write `results.jsonl` (one row per case and system, with diagnosis, trace, raw model replies, stop reason,
     tokens, LLM calls, tool calls, wall time), `summary.json` (with Wilson 95% CIs) and `table.md`.
   - Flags: `--limit`, `--cases`, `--sleep` (Groq rate limits), `--env`.
5. `bench/run.py` (synthetic): add the agent (through the simulator adapter), tool calls, wall time, stop-reason
   counts, paired comparisons vs `static_k3`, and `--systems`.
6. `scripts/run_m2_eval.sh`: one script for every table. `OFFLINE=1` covers the simulator runs, the audit and
   fix-check validation; otherwise it adds live gpt-oss-20b on all 32 real bugs x 5 systems, and on 40 synthetic
   cases.
7. Extension, root-cause scoring: a written rubric (correct / right file but wrong cause / unsupported / malformed)
   and a hand audit of at least 10 live diagnoses, with agreement noted.

Tests:
- `run_case` with a fixed fake LLM on `examples/payment_bug` scores every system;
- `summarize` and `wilson`;
- every name in `systems.py` builds (the agent skipped if langgraph is missing);
- `baseline` sets the stop reason.

Experiment: run the full pipeline on the merged `main`: simulator (2,000 cases and 300 large repos) and live
Groq (start with `LIMIT=5`). Every number in the report comes from these files.

Report (mine): Section 3 (setup, metrics, results tables, analysis of where adaptation helps, ties or hurts) and
assembling the final docx from everyone's sections (5 to 10 pages; every number checked against `results/`).

Done when: my PRs are merged; `results/m2/` has the full offline run plus the live real-bug run; Section 3 is in;
the report is assembled.
