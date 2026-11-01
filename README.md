# adaptive-context

Adaptive context management for LLM-based Python debugging: **adaptive retrieval, compression, sequencing, and token-budget management**,
plus a benchmark that scales to thousands of generated bug cases and repos with thousands of chunks.
Zero required dependencies (pure standard library). Python 3.10+.

## Limitations of a static-RAG baseline -> what fixes them

| Limitation | Fix in this repo | Where |
|---|---|---|
| Retrieves once, fixed Top-K | Iterative loop: model can `request` symbols/queries each round | `adaptive.py` |
| Same context size for easy and hard bugs | Retrieval grows only when evidence is missing; stops when request finds nothing new | `adaptive.py` |
| Embedding-only search misses linked files | Exact symbol lookup + call-graph prefetch of callees (`--prefetch`) | `retrieval.py`, `adaptive.py` |
| No compression | 4 levels: full / trimmed / outline / signature (first docstring line always kept) | `compress.py` |
| No token budget | Pack under a budget: compress then drop lowest-priority chunks first; requested symbols are pinned | `budget.py` |
| No ordering | Callers before callees (or by score / most relevant last) | `sequencing.py` |
| Failing-test evidence missing | `run_failing_test` runs pytest in a temp copy with timeout; output added to bug report (`--run-test`). `Tools` class holds all six tools (`search_code`, `grep_symbol`, `read_function`, `get_callers`, `get_callees`, `run_failing_test`) | `tools.py` |
| One test case only | Deterministic generator: any number of cases, repo size, bug depth, distractors; executable fix check | `bench/generate.py` |

## Quick start

```bash
pip install -e ".[dev]"            # no runtime deps
pytest                             # 14 tests, <1 s

# scale test, offline (simulated LLM). 2000 cases ~ 1 min on 1 CPU; use --workers N for more
python -m adaptive_context.bench.run --n 2000 --workers 4 --miss 0.1
python -m adaptive_context.bench.run --n 300 --modules 300 --chains 400     # ~1500 chunks per repo
python -m adaptive_context.bench.run --n 500 --budget 300                   # watch compression trade-off

# real repo + real LLM (Groq)
cp .env.example .env               # add GROQ_API_KEY
python -m adaptive_context.cli debug examples/payment_bug --bug-report-file examples/payment_bug/bug_report.txt --mode baseline
python -m adaptive_context.cli debug examples/payment_bug --bug-report-file examples/payment_bug/bug_report.txt --mode adaptive

# real LLM on generated cases (rate limits apply)
python -m adaptive_context.bench.run --llm groq --n 30 --workers 1
```

Benchmark systems: `static_k3` (baseline), `static_wide` (static, Top-10: does "just retrieve more" win?),
`adaptive` (LLM-driven requests), `adaptive_graph` (requests + callee prefetch).
Metrics: file/function accuracy with Wilson 95% CI, grounded accuracy (right answer AND evidence in context),
fix check (patched line is run against the failing test), tokens, LLM calls, retrievals, latency, failures, accuracy by bug depth,
paired counts.

## Results seen while building (simulated LLM, miss rate 10%)

| run | static_k3 | static_wide | adaptive | adaptive_graph |
|---|---|---|---|---|
| 2000 cases, ~126 chunks/repo: func acc | 0.184 | 0.227 | 0.898 | 0.894 |
| ... mean tokens/case | 633 | 1519 | 2583 | 1464 |
| 300 cases, ~1484 chunks/repo: func acc | 0.187 | 0.203 | 0.913 | 0.903 |
| ... mean tokens/case | 624 | 1509 | 2772 | 1730 |

Budget sweep (500 cases, no noise): accuracy stays 1.00 down to a 600-token budget, then falls to ~0.55 at 300 tokens
(compression hides the evidence). Tune `--budget` per model.

## Read this before quoting numbers

* **The offline LLM is a simulator** (`sim.py`). It follows the call chain and compares docstring vs `return` line; it never sees ground truth.
  It measures how well *context management* delivers evidence, not how smart a real model is. Real numbers need `--llm groq` runs.
* The ~0.9 ceiling is the simulated 10% miss rate, not a pipeline limit.
* Generated bugs are one kind (docstring contract vs return line). Real bugs are messier: add real repos (e.g. BugsInPy, SWE-bench Lite subset) next.
* Token counts are a 4-chars-per-token estimate in sim mode; Groq runs use the API's usage numbers.
* `ChromaRetriever` is included (`--retriever chroma`, `pip install -e ".[chroma]"`) but was **not run** here (needs a model download).
* `GroqLLM` was **not run** here (no network to Groq from the build sandbox). It uses the OpenAI-compatible endpoint with retry on 429/5xx.
* Fix verification only accepts `return <name> <op> <int>` lines and never executes other model text.

## Layout

```
src/adaptive_context/
  chunking.py retrieval.py compress.py budget.py sequencing.py protocol.py
  baseline.py adaptive.py llm.py sim.py cli.py models.py tokens.py
  bench/generate.py bench/run.py
tests/test_core.py        examples/payment_bug/

## Not done yet (honest status)

* No LangGraph loop: the agent loop is plain Python and the LLM requests symbols/queries; the `Tools` class is not yet wired in as LLM-callable function calls.
* No `fix_check` for real repos (only the synthetic benchmark verifies fixes).
* No real-repo datasets (BugsInPy etc.) and no real-LLM results yet.
* `run_failing_test` uses a temp copy and timeout, which is not a security sandbox. Only run tests from repos you trust.
```

## Mapping to the Milestone 2 design

| Design item | Code |
|---|---|
| Code index (AST chunks, symbols, call graph) | `chunking.py`, `retrieval.py` |
| Tools (`search_code`, `grep_symbol`, `read_function`, `get_callers/callees`, `run_failing_test`) | `tools.py` (index tools + sandboxed pytest) |
| Agent loop (plan, act, update context, diagnose) | `adaptive.py` (plain Python loop; **not LangGraph yet**) |
| Context manager (compression, sequencing, budget) | `compress.py`, `sequencing.py`, `budget.py` |
| Baseline | `baseline.py` |
| Eval runner | `bench/run.py` (generated cases). **Not yet:** runner over real cases + `fix_check` on real repos |

Known gaps: the loop requests symbols/queries directly rather than calling `tools.py` through function calling; the pytest-based fix check
exists only for generated cases (`bench/generate.py:verify_fix`).
# Adaptive-Context-for-LLM-Based-Software-Debugging
