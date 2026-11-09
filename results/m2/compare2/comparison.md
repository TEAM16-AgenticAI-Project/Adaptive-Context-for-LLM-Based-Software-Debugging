# Baseline vs proposed: comparison

Generated 2026-11-08 16:39 from `results/m2/compare2/stakeholder`, `results/m2/compare2/thefuck`. Backend: groq (openai/gpt-oss-20b). Bugs: 6.

## All bugs

| System | Role | Bugs | Right file | Right function | Fix passes tests | Avg tokens | Avg LLM calls | Avg time (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Static Top-3 (stakeholder baseline) | baseline | 6 | 83% (5/6) | 67% (4/6) | 33% (2/6) | 912 | 1.0 | 4.1 |
| Static Top-10 (wider baseline) | baseline | 6 | 83% (5/6) | 83% (5/6) | 33% (2/6) | 2205 | 1.0 | 11.1 |
| Adaptive retrieval loop | proposed | 6 | 67% (4/6) | 67% (4/6) | 33% (2/6) | 671 | 0.7 | 4.7 |
| Agent + fix check (ours) | proposed | 6 | 100% (6/6) | 83% (5/6) | 67% (4/6) | 15121 | 5.8 | 129.1 |

## stakeholder

| System | Role | Bugs | Right file | Right function | Fix passes tests | Avg tokens | Avg LLM calls | Avg time (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Static Top-3 (stakeholder baseline) | baseline | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 550 | 1.0 | 0.6 |
| Static Top-10 (wider baseline) | baseline | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 701 | 1.0 | 0.5 |
| Adaptive retrieval loop | proposed | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 614 | 1.0 | 0.5 |
| Agent + fix check (ours) | proposed | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 800 | 1.0 | 2.6 |

## bugsinpy-thefuck

| System | Role | Bugs | Right file | Right function | Fix passes tests | Avg tokens | Avg LLM calls | Avg time (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Static Top-3 (stakeholder baseline) | baseline | 4 | 75% (3/4) | 50% (2/4) | 0% (0/4) | 1092 | 1.0 | 5.9 |
| Static Top-10 (wider baseline) | baseline | 4 | 75% (3/4) | 75% (3/4) | 0% (0/4) | 2956 | 1.0 | 16.4 |
| Adaptive retrieval loop | proposed | 4 | 50% (2/4) | 50% (2/4) | 0% (0/4) | 699 | 0.5 | 6.8 |
| Agent + fix check (ours) | proposed | 4 | 100% (4/4) | 75% (3/4) | 50% (2/4) | 22282 | 8.2 | 192.4 |

## Per bug

| Bug | Files in the fix | Static Top-3 | Static Top-10 | Adaptive retrieval loop | Agent + fix check |
| --- | --- | --- | --- | --- | --- |
| payment_bug | 1 | **fix passes** | **fix passes** | **fix passes** | **fix passes** |
| auth_bug | 1 | **fix passes** | **fix passes** | **fix passes** | **fix passes** |
| thefuck-16 | 4 | right function | right function | right function | right function |
| thefuck-17 | 2 | right file | right function | missed | right file |
| thefuck-23 | 1 | right function | right function | right function | **fix passes** |
| thefuck-29 | 1 | missed | missed | missed | **fix passes** |

## Why one retrieval is not enough (LLM-free audit)

On all 32 real bugs of `data/real/thefuck.jsonl`, static BM25 retrieval puts a buggy function in the top 3 for 34% of the bugs and in the top 10 for 59%; every buggy function is in the top 3 for 25% (top 10: 50%). A single-shot Top-K baseline cannot fix code it never sees.

## How to read this

- **Same conditions for every system:** the same model, the same repository index (BM25 over function-level chunks)
  and the same bug report. Evaluator-only gold labels (the maintainer's fix locations) are used for scoring and are
  never shown to a system.
- **Right file / Right function:** the diagnosed location matches a file / function changed by the maintainer's fix.
- **Fix passes tests:** the system's final fix (every place it changes, in any file) is applied to a temporary copy,
  then the bug's failing test and the related test files are run. It passes only if the failing test passes and no
  related test breaks. The same check runs for the baselines, which get no feedback from it; `agent_verify` uses it
  inside its loop and may repair its fix up to the configured number of times.
- **Tokens / LLM calls / time:** averages per bug, input plus output tokens.
