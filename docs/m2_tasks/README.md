# Milestone 2: five equal tasks

Due **Nov 8, 2026, 11:59 PM Phoenix**. One group report (5 to 10 pages) built on the official template: stakeholder
follow-up, implemented agentic system, preliminary results (system vs at least two baselines on at least two
datasets), an actual demo, an actual failure, remaining work, and repo/contribution details.

Each person owns one area end to end: the code, its tests, one experiment, one report section, and their part of
the stakeholder demo. Everyone commits on their own branch and opens their own PR, so every contribution is visible.
Your personal prompt is in this folder: paste it into your AI assistant as the first message.

| Person | Area | Code you own | Experiment | Report |
|---|---|---|---|---|
| **Prem** | LangGraph agent + tools as function calls | `agent_graph.py`, `llm.py`, `tools.py` (Tools class), `cli.py` | Live gpt-oss-20b demo traces on 3 real bugs | Sec. 1 (meeting lead + notes), Sec. 2 |
| **Yadnesh** | Context manager | `budget.py`, `compress.py`, `sequencing.py`, `chunking.py`, `retrieval.py`, `adaptive.py` | Budget ablation + name-ambiguity stats | Sec. 5 (failure 1), ablation table |
| **Tanay** | Verification (fix check) | `fixcheck.py`, `bench/audit.py --validate-fixcheck` | Fix-check validation on all 32 gold fixes | Sec. 4 (fix-check case), Sec. 5 (failure 2), slides |
| **Karthik Kunduru** | Evaluation harness + live results | `baseline.py`, `bench/systems.py`, `bench/run_cases.py`, `bench/run.py`, `scripts/run_m2_eval.sh` | All synthetic + live runs, root-cause audit | Sec. 3 (results, analysis), report assembly |
| **Deep** | Datasets + baselines | `bench/audit.py --audit`, `bench/stakeholder_baseline.py`, new case manifests | Evidence audit, third baseline, BM25 vs Chroma | Sec. 3.1 (datasets, baselines), Sec. 6, Sec. 7 table |

## Setup (everyone, once)

```bash
git clone https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging.git
cd Adaptive-Context-for-LLM-Based-Software-Debugging
git checkout main && git pull                 # after m2-setup is merged
git checkout -b <your-name>/<area>            # e.g. yadnesh/context-manager
scripts/setup_env.sh                          # package + Python 3.8 test envs + 32 real bugs into data/real/repos
pytest -q && ruff check src tests             # must stay green on every PR
cp .env.example .env                          # add GROQ_API_KEY for live runs; never commit it
```

`setup_env.sh` prints the two `--env` flags that point at the Python 3.8 test environments. Every command that runs a
real case's tests needs them.

## What the setup already gives you

* `cases.py`: the case-manifest format, loader, and `score_diagnosis` (file, function, evidence, grounded).
  Gold labels live only in evaluator fields; **never** put them, or the maintainer's patch, in a prompt.
* `data/real/thefuck.jsonl`: 32 real bugs from BugsInPy/thefuck. Each one was checked to fail on the buggy source
  and pass with the maintainer's patch. The repos are rebuilt locally into `data/real/repos/` (git-ignored).
* `bench/bugsinpy.py`: the builder (reuse it for a second BugsInPy project).
* `tools.run_failing_test(repo, test_path=None, timeout, python=None, test_cmd=None)` and `run_pytest_in(...)`:
  run tests in a temporary copy with a chosen interpreter, with API keys removed from the environment.
* `models.py`: `RunStats.tool_calls`, `RunStats.wall_s`, `RunStats.over_budget_rounds`, `DebugResult.stop_reason`,
  `DebugResult.verification`, and `STOP_REASONS`.
* `docs/CODE_REVIEW.md`: what is wrong today and who fixes it.

## Shared interfaces (change one only after telling the team)

| Interface | Owner | Contract |
|---|---|---|
| Debugger | everyone | `run(bug_report: str) -> DebugResult`; sets `stop_reason` (one of `STOP_REASONS`) and `stats.wall_s` |
| Verifier | Tanay | `check_fix(repo, file, function, fix, *, test_path=None, test_cmd=None, python=None, timeout=120) -> {"applied", "mode", "passed", "reason", "output"}`; the agent takes any `Callable[[Diagnosis], dict]` with that shape |
| Lookup | Yadnesh | `retriever.lookup(name)` accepts `pkg.mod.func`, `pkg.mod.Class.method`, `path.py::Qual.name`, pytest ids `tests/t.py::TestX::test_y`, `Class.method`, short name; returns `list[Chunk]` |
| Packing | Yadnesh | `pack(scored, max_tokens, focus=(), allow_compress=True, pinned=frozenset()) -> PackResult(items, dropped, tokens, over_budget, dropped_pinned)`; `code_budget(total, *prompt_parts)` |
| LLM | Prem | `complete(system, user) -> LLMReply`; `chat(messages, tools=None, tool_choice=None) -> LLMReply` with `tool_calls: list[ToolCall(name, arguments, id)]` |
| Tools | Prem | `Tools.execute(name, args, exclude=frozenset()) -> ToolResult(ok, summary, chunks, pin, text)` |
| Systems | Karthik Kunduru | `make_system(name, retriever, llm, cfg, tools=None, verifier=None)`; names `static_k3`, `static_k10`, `adaptive`, `adaptive_graph`, `agent`, `agent_verify` |
| Case manifest | setup | `cases.py` (`RealCase`, `load_cases`, `score_diagnosis`) |

Until a dependency lands, code against the interface with a small stub or fake in your tests.

## Merge order

1. `m2-setup` (this branch).
2. Karthik's `kunduru` (action validation), as his own PR.
3. Yadnesh, Tanay and Deep: independent PRs.
4. Prem's agent: real-repo behaviour needs Yadnesh's lookup.
5. Karthik wires every system into the runners.
6. Full evaluation reruns once on the merged `main`. Every number in the report comes from that run.

## Dates

| When | What |
|---|---|
| Nov 1 to 3 | Build your area on your branch; first PR by Nov 3 |
| Nov 3 to 4 | Stakeholder meeting with the running system (Prem leads; everyone demos their part) |
| Nov 5 to 6 | Merge, full evaluation on `main`, screenshots |
| Nov 7 | Report sections in; Karthik assembles; Tanay finishes slides |
| Nov 8 | Freeze the commit, record it in Section 7, submit |

## Rules

* Stay inside your files. If you need a change elsewhere, ask that file's owner or open an issue.
* Small commits with real messages; PR to `main`; one teammate reviews each PR.
* `pytest -q` and `ruff check src tests` pass on every PR, with tests for what you add.
* Simulator numbers are labelled as simulator numbers and never mixed with live-model rows.
* Every number in the report comes from a file under `results/` produced by a committed revision.
* Generative AI is allowed if disclosed: add a line about your use to `docs/AI_ASSISTANCE.md` and keep your full
  interaction trace for the submission.

## About this setup

Prem prepared this setup branch, `docs/CODE_REVIEW.md` and these prompts with help from Claude (Anthropic). The
setup contains shared plumbing only; every area above is implemented by its owner on their own branch.
