# Section 5, Failure 1: the token budget hid the evidence (draft, Yadnesh)

All numbers below are **simulator numbers** (`SimulatedLLM`, miss rate 0, dataset A: generated call-chain bugs,
300 cases, seeds 0-299). They measure context management, not model quality. Sources, produced at commit
`1a66b12` (clean tree): `results/budget_ablation.json`, `results/budget_ablation_cases.jsonl`,
`results/budget_trace_seed0.json`, `results/name_ambiguity.json`.

Reproduce:

```bash
python -m adaptive_context.bench.budget_ablation --n 300 --out results
python -m adaptive_context.bench.budget_ablation --trace 0 --budgets 600 --out results
python -m adaptive_context.bench.name_ambiguity --manifest data/real/thefuck.jsonl --out results
```

## What went wrong

Before Milestone 2 the adaptive loop had a token budget that it did not really enforce:

1. **The budget counted code only.** The system prompt (219 tokens) and the bug report with the prompt scaffold
   (about 70 tokens) were not counted. At `token_budget=600` the real prompts reached **886 tokens**, and at 800
   they reached **1,079** (`600/legacy` and `800/legacy`, `max_prompt_tokens`).
2. **Under an honest budget, packing removed the evidence.** If the whole prompt has to fit in 600 tokens, about
   315 tokens are left for code. The old packer then compressed chunks to an outline or a signature by score
   alone. That included the function named in the bug report and the functions the model had asked for. On ties
   it also dropped the newest request first. Accuracy fell from 1.000 to **0.593** for the adaptive loop and
   **0.563** for the graph loop (`600/old_pack`).

Of the 122 cases that the old packing (`old_pack`) got wrong with the plain loop at 600, the buggy function was
missing from the final prompt in 62 and shown only as an outline in 55 (`bug_level` in
`budget_ablation_cases.jsonl`).

## One trace, before and after (seed 0, 600-token prompt, plain adaptive loop)

Bug report: *"`round_bulk_total(7)` returns 33 but the refund team expects 15."* The chain (evaluator-only) is
`round_bulk_total -> settle_weekly_credit -> normalize_bulk_credit -> apply_final_quota`, and the bug is in
`apply_final_quota` at depth 3.

**Before (old packing, whole-prompt budget).** Rounds 1 to 3 go well. The model asks for the next function in the
chain each round, and every one is shown trimmed. In round 4 the prompt holds six chunks and the packer must cut.
It outlines the lowest-scored ones, which include the entry function `round_bulk_total`. The model sees:

```python
def round_bulk_total(x):
    """Add 5 to the result of settle_weekly_credit."""
    ...
    return y + 5
```

The line `y = settle_weekly_credit(x)` is gone, so the chain cannot be followed from the entry point. The model
cannot connect the functions it already fetched. It gives up with a low-confidence guess at `round_bulk_total`,
which is wrong (`600/old_pack/adaptive`, stop reason `diagnosed`, confidence `low`).

**Fix.**

* `code_budget(total, system, scaffold)` reserves the prompt overhead, so `token_budget` is the whole prompt.
  The loop also re-packs if its estimate of the per-chunk header cost was off.
* `pack(..., pinned=...)`: requested chunks are never compressed below *trimmed*. Unpinned chunks are dropped
  first, and among pinned chunks the oldest request goes first (score `PIN + request index`).
* *Anchors:* functions from the first retrieval that the bug report names are protected like requests but rank
  below them.
* Every chunk is trimmed before any chunk is outlined, and outlines keep lines that call a repository function.
* Extension A: when pinned code must go, it is first replaced by a two-line *note* (signature, first docstring
  line, called names, return lines).
* `PackResult.over_budget` and `stats.over_budget_rounds` report any remaining overrun.

**After (new packing plus notes).** Rounds 1 to 3 are the same. In round 4 the entry function stays trimmed with
its call line, so all four chain functions are readable. The packer outlines an unrelated chunk to a signature
and drops another one. The model follows the chain to `apply_final_quota`, sees `"""Subtract 9 from the
input."""` next to `return x + 9`, and diagnoses it with high confidence and the correct fix
(`600/new_notes/adaptive`, prompt 557 tokens).

A first version of the fix protected only the requests. It scored 0.953 / 0.967 at 800, below old packing. The
cause was the same as above: the entry function was outlined. That result led to the anchors and to trimming
everything before outlining anything.

## Ablation table (simulator, 300 cases, function accuracy; adaptive / adaptive_graph)

| Prompt budget | legacy (code-only budget) | old packing | new packing | new + notes |
|---:|---|---|---|---|
| 600  | 1.000 / 1.000 (real prompt up to 886) | 0.593 / 0.563 | 0.980 / 0.980 | **1.000 / 1.000** |
| 800  | 1.000 / 1.000 (real prompt up to 1,079) | 1.000 / 0.993 | 1.000 / 1.000 | **1.000 / 1.000** |
| 1000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 1500 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| 3000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |

Mean tokens per case at 600 (adaptive / graph): old packing 1,643 / 1,083, new + notes 1,798 / 1,484. New packing
uses more rounds on deep bugs (3.00 vs 2.75 calls for the plain loop). In the three whole-prompt arms, no run went over budget. Only `legacy`, which counts code alone,
overran.

**Done-criterion check:** with notes, the plain loop and the graph loop at a 600-token prompt (1.000 / 1.000) are
no worse than at 800 today (1.000 / 1.000, whose real prompts were up to 1,079 tokens). The agent arm is not in the
table yet because Prem's agent has not merged.

Caveats:

* In one graph-loop case at 600 (seed 1), the correct diagnosis was made while the buggy function was shown only
  as a note. `score_diagnosis` / `evaluate` count evidence only at level 2 or better, so that case counts as
  correct but not grounded (grounded 0.997). This is conservative. `cases.py` is not changed here.
* The simulator follows calls by reading the code, so it gains a lot from keeping call lines. A real model may
  gain less. The live runs on dataset B are the real test.

## Related: name ambiguity on dataset B (32 thefuck repos, no model)

The old rule linked a call to every function with the same short name, and thefuck has up to 154 functions
called `match` (154 in `thefuck-1`). Import-aware resolution checks the same file first, then the file's imports, then falls back to the old
rule. Callee candidates per call site (`results/name_ambiguity.json`):

| Calls | n | median before | median after | unique before | unique after | mean before | mean after |
|---|---:|---:|---:|---:|---:|---:|---:|
| test code | 15,419 | 26 | **1** | 39.1% | **91.8%** | 51.5 | 1.7 |
| source code | 9,902 | 1 | 1 | 78.2% | 89.2% | 3.4 | 2.4 |
| all | 25,321 | 1 | 1 | 54.4% | 90.8% | 32.7 | 2.0 |

The maximum is still 154 after the change. Calls such as `rule.match(command)` on an object cannot be resolved
from imports and fall back to name matching.
