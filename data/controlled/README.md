# Controlled lexical-mismatch cases

Eight authored cases cover arithmetic, boundary predicates, stock clamping,
whitespace, list selection and ordering. They share a small call-chain scaffold:
public `gateway` entry → `relay.route` → `kernel.fold`. Distractor functions repeat
the public bug-report vocabulary. This deliberately constructed stress set is
not an independently sampled or held-out real-bug benchmark.

`cases.jsonl` follows the shared `cases.py` contract. `repos/` contains only buggy
code and pytest assertions. `patches/` is evaluator-only and outside the indexed
repository paths. Never put a patch or any `gold`/`source` field into a model prompt.

```bash
python -m adaptive_context.bench.controlled --out results/deep/controlled
python -m adaptive_context.bench.audit --audit --manifest data/controlled/cases.jsonl --out results/deep/controlled_audit
```

Validation copies each repo, runs pytest before the patch, applies the gold patch
to another temporary copy, and runs pytest after it. The original buggy sources
remain unchanged. Use `--build` only when intentionally regenerating fixtures;
it records the generator revision and overwrites these authored fixture files.
The checked-in results show eight genuine test failures before, eight passes
after, and zero LLM calls.
