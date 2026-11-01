# Starting prompt: Deep

Paste everything below into your AI assistant as the first message.

---

You are my pair programmer for my part of our team's CSE 598 Agentic AI capstone (ASU, Fall 2026),
Milestone 2, due Nov 8, 2026, 11:59 PM Phoenix. I am **Deep**. Five of us each own one area; my area is below.

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
I work on branch `deep/datasets-baselines`, created from `main` after the `m2-setup` merge. I have already run
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

## My task: datasets, the evidence audit, and the stakeholder's baseline as a third reference

Files I own: `src/adaptive_context/bench/audit.py` (the `--audit` part; Tanay owns `--validate-fixcheck`, so agree
on the layout with him), `src/adaptive_context/bench/stakeholder_baseline.py` (new), new manifests under
`data/` (e.g. `data/controlled/cases.jsonl` and a second BugsInPy project), and Chroma retriever tests.

Why: the report needs datasets described and justified, at least two baselines (the stakeholder's own pipeline
is the most meaningful third), and evidence for why one-shot retrieval fails on real bugs.

Build:
1. `audit.py --audit` (LLM-free), for each of the 32 real bugs. Write JSONL plus a summary.
   - Is the gold function in BM25 Top-3 / Top-10 of the bug report, and at what rank?
   - Does the test output name the gold file?
   - Does the failing test's module import the gold module (parse its imports)?
   - How many functions share the gold function's name?
2. `stakeholder_baseline.py`: run the stakeholder's original pipeline
   (https://github.com/YB-Yottabyte/adaptive-context, Chroma + 80-line windows + Top-3 + one gpt-oss-20b call)
   on dataset B.
   - Copy each case repo to a temp dir with its `bug_report.txt`, and run `uv run python src/baseline.py <dir>
     --provider groq --model openai/gpt-oss-20b --top-k 3`.
   - Parse "Relevant file" and the function from its output, and score with `cases.score_diagnosis`.
   - Raise its `--max-tokens` (512 can cut gpt-oss off) and record that.
   - Save the outputs. This gives one row next to our systems in the live table.
3. Controlled cases: 6 to 8 small multi-file bugs in the manifest format (`cases.py`). Make the bug report's
   wording NOT match the buggy file, so one-shot retrieval misses it. Each needs a failing pytest and gold
   file/function, and must fail before and pass after the fix.
4. A second BugsInPy project (PySnooper, tqdm or cookiecutter) via `python -m adaptive_context.bench.bugsinpy`
   with whatever test environment it needs. Report how many bugs reproduce.
5. `ChromaRetriever`: a test, and a BM25 vs Chroma comparison of gold recall at Top-3/10 on dataset B.

Tests:
- the audit on a tiny fixture repo;
- the stakeholder-output parser on a saved sample output;
- the controlled manifest loads, and every case fails before and passes after (gold) when a test runner is
  available.

Experiment: the evidence audit table, the third-baseline row (with Karthik's live run), and the retriever
comparison. Save everything under `results/`.

Report (mine):
- **Section 3.1:** datasets (source, task, size, how reproduced, why it matters to the stakeholder) and the
  baselines (what each does and why it is a fair comparison).
- **Section 6:** remaining work (gap, plan, owner, done-when).
- **Section 7:** the team contribution table (collect everyone's GitHub handle, commits/PRs and non-code work).

Done when: the audit and the stakeholder baseline run on all 32 bugs; at least one new dataset reproduces; my PR
is merged; my sections are in.
