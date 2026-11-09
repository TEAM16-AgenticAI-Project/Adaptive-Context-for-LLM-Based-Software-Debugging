# Baseline vs proposed: comparison

Generated 2026-11-09 00:54 from `results/m2/compare/stakeholder`, `results/m2/compare/thefuck_final`. Backend: groq (openai/gpt-oss-20b). Bugs: 8.

## All bugs

| System | Role | Bugs | Right file | Right function | Fix passes tests | Avg tokens | Avg LLM calls | Avg time (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Static Top-3 (stakeholder baseline) | baseline | 8 | 88% (7/8) | 75% (6/8) | 25% (2/8) | 1099 | 1.0 | 4.3 |
| Static Top-10 (wider baseline) | baseline | 8 | 88% (7/8) | 88% (7/8) | 25% (2/8) | 2331 | 1.0 | 12.4 |
| Adaptive retrieval loop | proposed | 8 | 62% (5/8) | 62% (5/8) | 25% (2/8) | 742 | 0.8 | 9.7 |
| Agent + fix check (ours) | proposed | 8 | 100% (8/8) | 88% (7/8) | 75% (6/8) | 18953 | 6.5 | 145.7 |

## stakeholder

| System | Role | Bugs | Right file | Right function | Fix passes tests | Avg tokens | Avg LLM calls | Avg time (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Static Top-3 (stakeholder baseline) | baseline | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 550 | 1.0 | 0.5 |
| Static Top-10 (wider baseline) | baseline | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 701 | 1.0 | 0.4 |
| Adaptive retrieval loop | proposed | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 614 | 1.0 | 0.5 |
| Agent + fix check (ours) | proposed | 2 | 100% (2/2) | 100% (2/2) | 100% (2/2) | 800 | 1.0 | 2.5 |

## bugsinpy-thefuck

| System | Role | Bugs | Right file | Right function | Fix passes tests | Avg tokens | Avg LLM calls | Avg time (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Static Top-3 (stakeholder baseline) | baseline | 6 | 83% (5/6) | 67% (4/6) | 0% (0/6) | 1282 | 1.0 | 5.6 |
| Static Top-10 (wider baseline) | baseline | 6 | 83% (5/6) | 83% (5/6) | 0% (0/6) | 2875 | 1.0 | 16.3 |
| Adaptive retrieval loop | proposed | 6 | 50% (3/6) | 50% (3/6) | 0% (0/6) | 784 | 0.7 | 12.8 |
| Agent + fix check (ours) | proposed | 6 | 100% (6/6) | 83% (5/6) | 67% (4/6) | 25004 | 8.3 | 193.4 |

## Per bug

| Bug | Files in the fix | Static Top-3 | Static Top-10 | Adaptive retrieval loop | Agent + fix check |
| --- | --- | --- | --- | --- | --- |
| payment_bug | 1 | **fix passes** | **fix passes** | **fix passes** | **fix passes** |
| auth_bug | 1 | **fix passes** | **fix passes** | **fix passes** | **fix passes** |
| thefuck-2 | 1 | right function | right function | missed | **fix passes** |
| thefuck-5 | 1 | right function | right function | right function | **fix passes** |
| thefuck-16 | 4 | right function | right function | right function | right function |
| thefuck-17 | 2 | right file | right function | missed | right file |
| thefuck-23 | 1 | right function | right function | right function | **fix passes** |
| thefuck-29 | 1 | missed | missed | missed | **fix passes** |

## Why one retrieval is not enough (LLM-free audit)

On all 32 real bugs of `data/real/thefuck.jsonl`, static BM25 retrieval puts a buggy function in the top 3 for 34% of the bugs and in the top 10 for 59%; every buggy function is in the top 3 for 25% (top 10: 50%). A single-shot Top-K baseline cannot fix code it never sees.

## Single runs reported separately (not in the tables)

| Run | Result | Files changed | LLM calls | Tokens | Time (s) |
| --- | --- | --- | --- | --- | --- |
| thefuck-16, gpt-oss-120b | verified | thefuck/shells/bash.py, thefuck/shells/zsh.py | 20 | 58592 | 365 |

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

## Notes on this run

- **Runs:** each real bug is taken once, from the first run in which it finished without hitting Groq's free-tier
  daily token cap: thefuck-2, thefuck-5 and thefuck-16 from the first run, thefuck-17, thefuck-23 and thefuck-29 from
  the second. Raw rows: `results/m2/compare/thefuck/` and `results/m2/compare2/thefuck/`.
- **Why fixes failed (real bugs):** static Top-3 {'apply_failed': 5, 'tests_fail': 1}; static Top-10 {'apply_failed': 4, 'tests_fail': 2};
  adaptive {'no_fix': 3, 'apply_failed': 3}; agent + fix check {'related_failing': 1, 'tests_fail': 1}. `apply_failed` means the
  proposed fix could not be applied to the file as written (for example a partial snippet), `tests_fail` that it
  applied but the failing test still fails, `related_failing` that the failing test passes but a related test still
  fails, `no_fix` that the run ended without a fix.
- **Adaptive loop:** 3 of its 6 real-bug runs ended with Groq rejecting the model's JSON output (HTTP 400
  "Parsing failed"); they count as misses, so its row is pessimistic.
- **Cost:** the agent spends about 20x the
  tokens of static Top-3 on the real bugs; times include waits for the free tier's per-minute token limit.
- **Size:** 8 bugs is a small sample; treat the percentages as indicative.
