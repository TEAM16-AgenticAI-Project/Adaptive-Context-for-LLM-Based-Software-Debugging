# Starting prompt: Prem

Paste everything below into your AI assistant as the first message.

---

You are my pair programmer for my part of our team's CSE 598 Agentic AI capstone (ASU, Fall 2026),
Milestone 2, due Nov 8, 2026, 11:59 PM Phoenix. I am **Prem**. Five of us each own one area; my area is below.

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
I work on branch `prem/agent`, created from `main` after the `m2-setup` merge. I have already run
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

## My task: the LangGraph agent, with the tools as native function calls

Files I own: `src/adaptive_context/agent_graph.py` (new), `llm.py`, the `Tools` class in `tools.py`, `cli.py`.

Why: the tools exist in `tools.py`, but the model cannot call them. The adaptive loop in `adaptive.py` is plain
Python and parses JSON requests. Milestone 2 needs a real agent: planning, tool use, state, verification and
recovery.

Build:
1. `llm.py`: a `ToolCall(name, arguments, id)` dataclass and `LLMReply.tool_calls`. `GroqLLM.chat(messages,
   tools=None, tool_choice=None)` uses Groq's OpenAI-compatible `tools` / `tool_choice`; keep `complete()` working.
   gpt-oss-20b spends output tokens on hidden reasoning, so default `max_tokens` to about 2048 and send
   `reasoning_effort="low"`; if Groq rejects that parameter with HTTP 400, retry once without it. Keep the
   429/5xx retry logic.
2. `tools.py`: OpenAI-format schemas for `search_code`, `grep_symbol`, `read_function`, `get_callers`,
   `get_callees`, `run_failing_test` (offered only when a test is configured), plus a `diagnose` schema
   (file, function, cause, fix, confidence high|low). Add `Tools.execute(name, args, exclude) -> ToolResult(ok,
   summary, chunks, pin, text)`: it validates arguments and returns a clear error for unknown tools or bad
   arguments. `read_function` results are pinned. When a name matches more than about 6 functions (thefuck has
   136 named `match`), add nothing and tell the model to use the module path, e.g. `thefuck.rules.git_push.match`
   (Yadnesh's `lookup` resolves those). `Tools` also takes `python=` and `test_cmd=` for real repos.
3. `agent_graph.py`: a LangGraph `StateGraph` with nodes `seed -> context -> plan -> act -> context ...`, and
   `plan -> verify -> END`.
   - **seed:** BM25 Top-k of the report. Pin the failing test if the report names a pytest id, and add its test
     module's imports.
   - **context:** calls Yadnesh's `pack(..., pinned=...)` and `order(...)` and rebuilds a FRESH prompt each round
     (bug report, last ~12 history lines, test output, packed code). Tool output must not pile up in a chat log.
   - **plan:** one `chat()` call with the schemas. On the last round, or after a round with no new evidence, offer
     only `diagnose` and force it via `tool_choice`. Text replies in the old JSON protocol (`protocol.parse_reply`)
     are accepted as a fallback. Invalid replies get a corrective note, up to 2 retries, then stop with
     `invalid_exhausted`.
   - **act:** run at most 3 tool calls per round and log one-line results to history.
   - **verify:** optional; takes any `verifier(diagnosis) -> dict` (Tanay's `check_fix` fits). A failing fix is fed
     back once for revision, then the run stops with `verify_failed`; a passing fix stops with `verified`.
   - Set `stop_reason`, `stats.tool_calls`, `stats.wall_s`, and a per-round trace (tool calls, results, context
     levels, prompt tokens).
   - Add a `SimToolLLM` adapter so the offline simulator can drive the graph for tests and dataset A.
4. `cli.py`: `--mode agent` and `--mode agent_verify`, and `--case data/real/thefuck.jsonl:thefuck-5` (takes the
   repo, report, test command and test Python from the manifest; `--env NAME=PYTHON` repeatable).

Tests (scripted fake LLM that returns tool calls):
- tool call, then diagnose;
- the last round offers only `diagnose` and forces it;
- invalid replies, then recovery, and running out of retries;
- the legacy JSON text fallback;
- the verifier feeds a failing fix back, then `verified`;
- the failing test named in the report is pinned;
- `execute` rejects unknown tools and bad arguments;
- an ambiguous name adds nothing.

Experiment: live gpt-oss-20b runs of `agent_verify` on thefuck-5, one shell bug (thefuck-3) and one core bug
(thefuck-29). Save the JSON traces under `results/` and take screenshots. These are the Section 4 live demo.

Report (mine):
- **Section 2:** workflow, the updated architecture diagram (implemented vs planned), and a table of the agentic
  mechanisms (when each runs, what it reads or updates, how it changes the next action). Also why a single agent
  with an execution-based verifier rather than multi-agent, and the models and frameworks used.
- **Section 1:** I lead the stakeholder meeting. Record the date, participants and notes, and a
  Feedback → Team response → Next action table.

Done when: the tests pass; `--case` runs end to end on a real bug with Groq; my PR is merged; Sections 1-2 are
drafted.
