# Phase 4 plan: performance and algorithm improvements

This plan divides the next round of work so that each person owns different files and no
two tasks edit the same file. New behavior goes behind a flag that defaults to current
behavior, so turning it on is a deliberate team decision, never an accident that moves
someone else's results. Each person works on their own branch and opens their own PR;
`pytest -q` and `ruff check src tests` must pass before merge.

The research behind these choices (repository-level LLM debugging): graph-based multi-hop
localization and smarter context packing are what separate state-of-the-art systems from
plain lexical retrieval (for example KGCompass, ARISE, SHERLOC, and role-aware retrieval
representations). The common thread: exploit the call/data graph, and be deliberate about
what enters the context window; smaller, smarter context is both more accurate and cheaper.

## Why this order

Model calls dominate evaluation wall time, and the free-tier rate limit plus single-worker,
serial execution is why a 10-case run took about 25 minutes. That is an infrastructure
bottleneck, not an algorithm one. The speed layer is therefore first, because it unblocks
everyone: once evaluations run in minutes, every other improvement can be measured quickly
as an ablation. The algorithm and model work then proceed in parallel in separate files.

## Ownership (one owner per file, no overlaps)

### Karthik (kkundur1) - speed layer and evaluation, files under `bench/` only
- Done: `bench/cache.py` (CachingLLM), merged/PR'd. Identical model calls are served from a
  JSON-lines cache, so re-runs and repeated sub-prompts cost no network.
- Next: wire the cache into `bench/run.py` and `bench/run_cases.py` behind a `--cache PATH`
  flag (default off), and add request-paced parallelism so `--workers N` can run many cases
  without tripping the rate limit.
- Safe because: all in `bench/`; it wraps the LLM at the call site and does not change the
  retriever, compressor, or loop.
- Interface contract: the cache wraps any object exposing `complete`/`chat` with the current
  `LLMReply` shape. Keep that signature stable (that is Prem's `llm.py` contract below).

### Yadnesh (yashpy) - algorithm: graph-primary localization and role-aware summaries
- Files: `adaptive.py`, `retrieval.py`, `compress.py`, `sequencing.py` (your area).
- Graph-primary localization: make the call graph the primary driver. Start at the entry
  function the bug report names, walk callees/callers along the dependency path, and use
  lexical search as a fallback rather than the main signal. You already own `callees()` and
  `resolve_call()`, so this builds on existing code.
- Role-aware summaries: in `compress.py`, summarize a function's role instead of truncating
  raw text. This improves accuracy and shrinks prompts at the same time.
- Safe because: these are your files already; no one else edits the retriever or compressor.
- Guardrail: both go behind flags (for example `localization="graph"` and
  `compress="role_summary"`) that default to today's behavior, so results do not move until
  the team flips them on and re-evaluates.

### Prem (prempagare07) - model tiering and provider flexibility
- Files: `llm.py`, `tools.py`, `agent_graph.py` (your area).
- Cheap-planner / expensive-solver: use a small/fast model (or a heuristic) for the "is the
  context sufficient?" decision and reserve the large model for the actual diagnosis. Fewer
  large-model calls, lower latency.
- Early-exit on confidence: if the model is high-confidence at round 1, stop instead of
  spending more rounds.
- Provider abstraction: make swapping Groq for a higher-rate-limit provider a one-line change
  behind the existing `complete`/`chat` interface.
- Safe because: all your files. Keep `complete(system, user)` and
  `chat(messages, tools, tool_choice)` returning the current `LLMReply`, so Karthik's cache
  keeps wrapping it transparently.

### Tanay (Tanay126) - verification depth and candidate re-ranking
- Files: `fixcheck.py` (yours) and a new `bench/rerank.py`.
- Stronger verification (your Section 5.2 remaining item): run the whole test module and check
  whether the fix touched the gold function, to flag overfitted "passes but wrong" fixes.
- LLM re-ranking: a new `bench/rerank.py` that takes a candidate `list[Chunk]` and returns a
  re-ranked `list[Chunk]` via one cheap model call. New standalone file, collides with nobody.
- Safe because: `fixcheck.py` is yours and `rerank.py` is new.
- Interface contract: the re-ranker takes and returns `list[Chunk]`; Yadnesh's retriever hands
  it the candidate pool and consumes the reordered list (behind a flag).

### Deep (Deep-nayak007) - data scale and the real benchmark
- Files: `bench/bugsinpy.py`, dataset manifests, `bench/stakeholder_baseline.py` (your area).
- Expand the real-bug set beyond 32 (more BugsInPy projects) so the evaluation has enough
  cases for real confidence intervals once the speed layer makes large runs feasible.
- Fix the Chroma repeated-AST-ID issue so the stakeholder baseline indexes cleanly.
- Run the stakeholder baseline live now that a key is available.
- Safe because: your dataset and adapter files; no overlap with the others.

## Integration order

1. Karthik's cache + parallelism lands first (unblocks fast iteration).
2. Yadnesh, Prem, Tanay, Deep work in parallel in their own files, each behind a default-off
   flag where behavior changes.
3. Integration last: the team flips the flags on and re-runs the full evaluation once on merged
   main (Karthik's harness). Every reported number comes from that run.

## The three rules that keep it from breaking

1. One owner per file (the table above). If your task needs a change in someone else's file,
   ask that owner to add a hook and code against it with a stub until it lands.
2. New behavior is flag-gated and defaults to current behavior, so no change silently moves
   another person's results.
3. Own branch, own PR, one reviewer, `pytest -q` and `ruff` green before merge. Simulator,
   live-model, and retrieval-only numbers stay in separate tables.

## Shared interface contracts to agree before starting

- LLM: `complete(system, user) -> LLMReply` and `chat(messages, tools, tool_choice) -> LLMReply`
  with the current `LLMReply` fields. Owner: Prem. Consumed by: everyone, and wrapped by
  Karthik's cache.
- Re-ranker: `rerank(candidates: list[Chunk], bug_report: str) -> list[Chunk]`. Owner: Tanay.
  Consumed by: Yadnesh's retrieval behind a flag.
- Retriever graph navigation: `callees(chunk)` / `resolve_call(...)` already exist. Owner:
  Yadnesh. Consumed by: the adaptive loop.
