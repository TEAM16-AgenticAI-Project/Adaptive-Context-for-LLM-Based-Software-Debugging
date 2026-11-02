# Deep's Milestone 2 artifacts

These files are checked in deliberately despite the repository's general
`results/` ignore rule. All evaluation numbers below come from the referenced
JSON/JSONL files. Source revisions are stored in each summary.

The latest [integration recheck](recheck/README.md) preserves both the evidence
audit and Tanay's verifier on one branch and includes a clean-checkout gate.

| Artifact | Scope and producing revision |
| --- | --- |
| `audit/summary.json`, `audit/audit.jsonl` | Original 32-case, LLM-free BM25 evidence audit; `b8e5ec0` |
| `retrievers/summary.json`, `retrievers/audit.jsonl` | Untouched Chroma comparison; all 32 attempted, 27 completed, 5 duplicate-ID errors; `b8e5ec0` |
| `retrievers_deduplicated/summary.json`, `retrievers_deduplicated/audit.jsonl` | Explicit last-definition input variant for both rankers, all 32 completed; `1124a2a` |
| `controlled/summary.json`, `controlled/validation.jsonl` | Eight actual fail-before/pass-after checks; `b8e5ec0` |
| `controlled_audit/summary.json`, `controlled_audit/audit.jsonl` | Lexical-mismatch construction check on all eight cases; `b8e5ec0` |
| `datasets.json` | Builder reproduction metadata: thefuck 32/32; PySnooper 2/3; manifest hashes, source pins and package versions; `1124a2a` |
| `stakeholder/summary.json`, `stakeholder/results.jsonl` | No live model run: 32 explicit `not_run` rows, metrics null; `b8e5ec0` |
| `contributions.md` | Generated Git-history inventory before the artifact/report commit; `1124a2a` source snapshot |
| `pytest.txt`, `ruff.txt` | Final local check output on the implemented code; see `verification.json` |

The original audit uses the supplied manifest hash
`905820e582f99bf8dc71a4248135eddaaa5c93f1730fa5cfee9d52aea9489f44`.
Freshly generated traceback wording can differ even for the same bug; it is not
silently substituted for those query texts. The fresh manifests and source pins
are recorded separately in `datasets.json`.

For both rankers, recall is the share of cases retrieving at least one evaluator
gold function at K (or every one for `all_at_k`). No labels or patches are used
as retrieval input. The unmodified comparison's denominator is only the 27 paired
completions, with five errors reported separately. The explicit preprocessing
variant has denominator 32. Its scores do not demonstrate that the unmodified
production Chroma path is fixed.

Groq accuracy, token billing and live fix-check success are not measured. The
control/test validation and embedding runs are not labeled as a full agent run.
See [the handoff](../../docs/report/deep_handoff.md) and
[experiment analysis](../../docs/report/deep_experiments.md).
