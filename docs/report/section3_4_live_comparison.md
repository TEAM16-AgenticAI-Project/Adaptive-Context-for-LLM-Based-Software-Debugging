# 3.4 Live comparison on real bugs, with every fix checked (openai/gpt-oss-20b via Groq; Prem)

Sections 3.1-3.2 score where a system looks. This run also checks whether its fix works. Every system gets the
same model, the same BM25 index over function chunks and the same bug report, with up to 8 rounds and a
4,000-token context budget (3.1 used 6 rounds and 3,000 tokens), and `agent_verify` may repair its fix up to 6
times. After each run, the same fix check is applied to every system's final fix: all places it changes are
applied to a temporary copy, then the failing test and the related test files are run. A fix passes only if the
failing test passes and no related test breaks. The baselines get no feedback from this check. Bugs: the
stakeholder's `payment_bug` and `auth_bug`, and 6 BugsInPy/thefuck bugs, two of them multi-file (thefuck-16,
thefuck-17).

Real bugs (6), averages per bug:

| System | Role | Right file | Right function | Fix passes tests | Fix passes, all 8 bugs | Tokens | LLM calls | Time (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| static_k3 | baseline | 5/6 | 4/6 | 0/6 | 2/8 | 1,282 | 1.0 | 5.6 |
| static_k10 | baseline | 5/6 | 5/6 | 0/6 | 2/8 | 2,875 | 1.0 | 16.3 |
| adaptive | proposed | 3/6 | 3/6 | 0/6 | 2/8 | 784 | 0.7 | 12.8 |
| agent_verify | proposed | 6/6 | 5/6 | 4/6 | 6/8 | 25,004 | 8.3 | 193.4 |

Per bug (fix passes / right function / right file / missed):

| Bug | Files in the fix | static_k3 | static_k10 | adaptive | agent_verify |
| --- | --- | --- | --- | --- | --- |
| payment_bug | 1 | passes | passes | passes | passes |
| auth_bug | 1 | passes | passes | passes | passes |
| thefuck-2 | 1 | function | function | missed | passes |
| thefuck-5 | 1 | function | function | function | passes |
| thefuck-16 | 4 | function | function | function | function |
| thefuck-17 | 2 | file | function | missed | file |
| thefuck-23 | 1 | function | function | function | passes |
| thefuck-29 | 1 | missed | missed | missed | passes |

**Analysis.** On these real bugs, finding the function was not the hard part: both static baselines named a buggy
function on 4-5 of 6 bugs, yet none of their fixes passed. In 9 of their 12 real-bug runs the fix could not even be
applied as written (for example a partial snippet), and the other 3 applied but the failing test still failed. The
agent with the fix check passed on 4 of 6 (thefuck-2, 5, 23, 29), including thefuck-29, where no other system found
the right function: it read the test failure and repaired its fix. The cost is about 20 times the tokens of
`static_k3` (25.0k vs 1.3k per real bug), about 8 LLM calls, and about 3 minutes per bug, which includes waits for
the free tier's per-minute token limit. Neither multi-file bug passed with gpt-oss-20b: on thefuck-16 the failing
test passed but a related test still failed, and on thefuck-17 the fix still failed the test when the run ran out of
valid replies. In a separate run reported outside the table, `openai/gpt-oss-120b` fixed thefuck-16 across
`bash.py` and `zsh.py`, with the failing and related tests passing (20 LLM calls, 58.6k tokens, 365 s).

**Caveats.** 8 bugs, one run each: the percentages are indicative only. Each real bug is taken from the first run
in which it finished without hitting Groq's free-tier daily token cap (thefuck-2, 5 and 16 from the first run; 17,
23 and 29 from the second). The adaptive row is pessimistic: 3 of its 6 real-bug runs ended when Groq rejected the
model's JSON output (HTTP 400). A fix that passes the tests is not proven correct (Section 5.2).

**Reproduce.** `scripts/run_comparison.sh` (Groq key in `.env`; Python 3.8 test environments from
`scripts/setup_env.sh`) writes `results/m2/compare/comparison.md`; the raw per-case rows are in
`results/m2/compare/` and `results/m2/compare2/`.

**Line for Section 4** (replaces "The per-system share of final fixes that pass comes from the completed live runs
(pending)."): In the live comparison (Section 3.4), the agent's final fix passed the failing test and its related
tests on 4 of 6 real bugs, against 0 of 6 for both static baselines and the adaptive loop.
