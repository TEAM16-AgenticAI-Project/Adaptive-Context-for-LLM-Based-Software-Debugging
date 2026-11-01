# Starting prompt: Yadnesh

Paste everything below into your AI assistant as the first message.

---

You are my pair programmer for my part of our team's CSE 598 Agentic AI capstone (ASU, Fall 2026),
Milestone 2, due Nov 8, 2026, 11:59 PM Phoenix. I am **Yadnesh**. Five of us each own one area; my area is below.

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
I work on branch `yadnesh/context-manager`, created from `main` after the `m2-setup` merge. I have already run
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

## My task: the context manager (budget, compression, ordering, lookup)

Files I own: `src/adaptive_context/budget.py`, `compress.py`, `sequencing.py`, `chunking.py`, `retrieval.py`,
`adaptive.py`.

Why (from `docs/CODE_REVIEW.md`, findings 2-8):
- Requested code can be compressed to a signature, so the model cannot read the function it asked for.
- `pack` can silently go over budget, and the budget ignores the system prompt and bug report.
- When pinned code has to be dropped, the newest request goes first.
- Module-level code is cut after 60 lines.
- Name lookup is ambiguous on real repos: thefuck has 136 functions called `match`.

Build:
1. `budget.py`:
   - `code_budget(total, *prompt_parts, floor=200)` reserves the prompt overhead.
   - `pack(..., pinned=frozenset())`:
     - pinned chunks are never compressed below level 1 ("trimmed");
     - unpinned chunks are dropped before pinned ones;
     - among pinned chunks, newer requests rank higher, so the oldest is dropped first;
     - `PackResult` gains `over_budget` and `dropped_pinned`.
2. `adaptive.py`: use `code_budget`, a pinned set, and recency priority for requested symbols. Set `stop_reason`
   (diagnosed / forced_diagnosis / round_limit / invalid_exhausted / llm_error), `stats.wall_s` and
   `stats.over_budget_rounds`. Validate positive arguments.
3. `chunking.py`: split module-level code into bounded chunks with no truncation (first keeps the id
   `path::<module>`, later ones `path::<module>@L<n>`). Silence `SyntaxWarning` when parsing old repos.
4. `retrieval.py`: `lookup` accepts dotted module paths as written in imports (`thefuck.rules.git_push.match`,
   `pkg.mod.Class.method`), `path.py::Qual.name`, pytest ids (`tests/t.py::TestX::test_y`), `Class.method` and
   short names, most specific first.
5. Extension A, notes for evicted code: when pinned code must be dropped, keep a one-line note in its place
   (signature, first docstring line and return lines) so the model does not lose what it already checked.
6. Extension B, import-aware call resolution: resolve a chunk's callees through its module's imports
   (`from x.y import f` means x/y.py::f) before falling back to name matching, so a test that imports
   `thefuck.rules.git_push.match` links to that one function, not 136.

Tests:
- pinned chunks stay at trimmed or better;
- the oldest pinned chunk is dropped first;
- an oversized single chunk sets `over_budget`;
- `code_budget` reserves overhead;
- a long module splits without losing lines;
- every lookup form resolves;
- import-aware callees pick the imported function on a two-module fixture where both define `match`;
- notes replace evicted pinned code.

Experiment:
- Budget ablation on dataset A with the simulator: budgets 600 / 800 / 1000 / 1500 / 3000, 300 cases each.
  Compare old packing (pinned compressible), new packing, and new packing plus notes, for the adaptive loop (and
  the agent once Prem's lands). Save the JSON under `results/`.
- Name ambiguity on dataset B: median callee candidates per call, before and after import-aware resolution.

Report (mine): Section 5, Failure 1, the budget failure. Show a real trace before and after the fix (which
evidence was dropped, why the run failed, the fix, and the rerun numbers), plus the ablation table.

Done when: the tests pass; with notes, the plain loop and the agent at a 600-token budget are no worse than at
800 today; my PR is merged; the Section 5 failure 1 draft is in.
