# Code review: team package and stakeholder baseline (Oct 31, 2026)

Reviewed: team `main` @ 6ffa0c0 (Yadnesh), Sai's `kunduru` @ 84eee57, stakeholder
[YB-Yottabyte/adaptive-context](https://github.com/YB-Yottabyte/adaptive-context) @ c545608.
Both suites pass as found (team 14/14 on main, 44/44 on kunduru; stakeholder 15/15, ruff clean).
The findings below are behaviour the tests do not cover. Each one has an owner in `docs/m2_tasks/`.

## Team package

| # | Finding | Impact | Owner |
|---|---|---|---|
| 1 | A reply like `{"action":"diagnose"}` became a *valid* diagnosis with an empty file | Inflated validity, hidden failures | Sai (already fixed on `kunduru`, merge it) |
| 2 | Requested symbols can be compressed down to a signature, so the model cannot read the function it asked for | Accuracy loss under tight budgets | Yadnesh |
| 3 | `pack` can return an over-budget prompt without saying so | Budget not enforced | Yadnesh |
| 4 | The budget counts code only; system prompt and bug report are ignored | Real prompts larger than configured | Yadnesh |
| 5 | When pinned evidence must be dropped, the *newest* request goes first (equal scores, stable sort) | Model re-requests the same function and the loop stalls | Yadnesh |
| 6 | Module-level code is truncated after 60 lines | Later imports/constants unreachable | Yadnesh |
| 7 | Name lookup is ambiguous on real repos: thefuck has 136 functions named `match`; `lookup("match")` returns all of them | Wrong evidence, wasted budget | Yadnesh (lookup forms) + Prem (tool-level guard) |
| 8 | Latency is the sum of LLM call times; no end-to-end time; no record of why a run stopped | Cannot report latency or failures | Setup adds the fields; Yadnesh (adaptive), Sai (baseline), Prem (agent) set them |
| 9 | `max_tokens=800` with gpt-oss-20b, whose hidden reasoning also uses output tokens | Risk of cut-off JSON | Prem |
| 10 | README headline numbers come from the simulator only | Could be misread as model results | Sai (report labels) |
| 11 | `run_failing_test` always used the current interpreter | Real repos need their own env | Fixed in setup (`python=`, `test_cmd=`) |
| 12 | Tools exist but the model cannot call them | Gap vs the design | Prem |
| 13 | No fix check for real repos | Cannot verify fixes | Tanay |
| 14 | No real-bug dataset or real-case evaluator | No real results | Dataset in setup; evaluator Sai; audit Deep |
| 15 | `ChromaRetriever` is untested | Unknown | Deep |

## Stakeholder baseline

| # | Finding | Impact | Recommendation (owner) |
|---|---|---|---|
| S1 | The prompt contains a worked example of exactly the `payment_bug` defect (caller discards a return value) | The one shown success is partly primed | Treat `payment_bug` as a sanity check; compare on dataset B (Deep) |
| S2 | Test cases have 3 to 4 files, so Top-3 is nearly the whole repo | Retrieval is not exercised | Use the 32 real bugs, ~1,000 chunks each (Deep) |
| S3 | 80-line windows with 20 lines overlap cut through functions | Partial functions in context | Mention in the comparison (Deep) |
| S4 | Re-embeds the whole repository on every run | Slow on real repos | Note the cost in the third-baseline run (Deep) |
| S5 | `max_tokens=512` default with a reasoning model | Possible truncated answers | Raise it for the third-baseline run (Deep) |
| S6 | No evaluation harness; terminal output only | No comparable numbers | Adapter + scoring on dataset B (Deep) |

Matched comparison note: our `static_k3` reproduces the stakeholder's design (one retrieval, Top-3, one call) on the
*same* chunking, retriever, prompt format and model as the adaptive systems, so differences come from adaptation.
The stakeholder's original Chroma pipeline differs in all of those and is reported as a separate, third baseline.
