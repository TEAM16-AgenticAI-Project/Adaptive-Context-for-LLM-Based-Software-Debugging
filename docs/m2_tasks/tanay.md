# Starting prompt: Tanay

Paste everything below into your AI assistant as the first message.

---

You are my pair programmer for my part of our team's CSE 598 Agentic AI capstone (ASU, Fall 2026),
Milestone 2, due Nov 8, 2026, 11:59 PM Phoenix. I am **Tanay**. Five of us each own one area; my area is below.

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
I work on branch `tanay/verification`, created from `main` after the `m2-setup` merge. I have already run
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

## My task: verification, the fix check on real repositories

Files I own: `src/adaptive_context/fixcheck.py` (new), `src/adaptive_context/bench/audit.py` (the
`--validate-fixcheck` part; Deep owns the `--audit` part of the same CLI, so agree on the file layout with him,
e.g. two functions plus one `main`).

Why: today only the synthetic benchmark can check a fix (`bench/generate.py:verify_fix`, a restricted
`return x + n` line). Real bugs need: apply the model's fix to a copy of the repo and rerun the failing test.

Build:
1. `fixcheck.py`, `apply_fix_to_source(source, function, fix) -> (new_source, mode)`. Accepted shapes:
   - a complete function (`def ...`, `async def ...` or a decorated function) that replaces the named function,
     found by AST with qualified names like `Class.method`;
   - a single line that replaces the most similar line inside that function (difflib ratio at least 0.5);
   - leading `import` / `from` lines followed by a function: the imports go after the module's last top-level
     import, skipping duplicates.
   Code fences are stripped. It raises `FixError(reason)` for `empty_fix`, `function_not_found`,
   `unsupported_fix_shape`, `fix_syntax_error`, `no_change`, `no_similar_line`.
2. `check_fix(repo, file, function, fix, *, test_path=None, test_cmd=None, python=None, timeout=120)`:
   - refuses paths outside the repo;
   - copies the repo to a temp dir (`tools.IGNORE`), writes the patched file, and runs
     `tools.run_pytest_in(...)` with the case's test command and Python;
   - returns `{"applied", "mode", "passed", "reason", "output"}` and never touches the original files;
   - this is the verifier Prem's agent calls.
3. Extension, multi-function and module-level fixes: 8 of the 32 real bugs change two or more functions, and some
   also change module-level data. Accept a unified diff as a fix (validate with `git apply --check` in the temp
   copy, then apply).
4. `audit.py --validate-fixcheck`: for every real case, build the gold fix as a model would write it (the
   maintainer's fixed function, plus any new imports, or the diff for multi-function bugs) and check it passes.
   Resubmit the buggy body as a negative control and check it is rejected or fails. Write JSONL plus a summary.

Tests (use `examples/payment_bug`):
- a correct function fix passes;
- a wrong fix fails with `tests_fail`;
- line mode;
- import hoisting;
- unsupported shapes are rejected;
- `../etc/passwd` is refused;
- `function_not_found`;
- the original files stay untouched;
- a diff fix applies.

Experiment: the validation on all 32 bugs. Target: every maintainer fix passes and every negative control is
rejected. Explain any that don't. Later, with Sai's live runs: the share of each system's final fixes that pass.

Report (mine):
- **Section 4:** the fix-check case study on a real bug (e.g. thefuck-5: the maintainer's fix passes, a
  plausible wrong fix fails, the unchanged body is rejected), with a screenshot of the actual run.
- **Section 5:** Failure 2, the verification limits, with a note that this executes model-written code in a temp
  copy with a timeout and no API keys, which is not a security sandbox.
- I also own the final slide deck.

Done when: the tests pass; the validation table is in `results/`; Prem's `agent_verify` uses my `check_fix`; my
PR is merged.
