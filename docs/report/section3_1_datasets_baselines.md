# Section 3.1 — Datasets and baselines (Deep)

The task is to localize a Python defect to its file and function using the bug
report and repository, then propose an evidence-grounded fix. Gold labels and
maintainer patches are evaluator-only. Retrieval receives the bug report, not
those fields. The stakeholder adapter receives only a temporary buggy checkout
and `bug_report.txt`.

| Dataset | Source and preparation | Current evidence and purpose |
| --- | --- | --- |
| A: generated call-chain bugs | The team's deterministic generator supplies bug reports, multi-file call chains, distractors, and executable checks. | A controlled test of context delivery and budget. The simulator is not a real LLM; Karthik supplies the final evaluated sample/configuration from his merged evaluation run. |
| B: BugsInPy/thefuck | 32 historical bugs; fixed revision exported with regression tests, maintainer patch reversed to construct buggy code, then reapplied for validation. | All 32 reproduce locally with Python 3.8 and the two configured pytest environments. The original report texts are retained for comparisons. Indexed snapshots contain 507–1,713 AST/module chunks, rather than a nearly whole-repository Top-3 selection. |
| Controlled lexical-mismatch stress set | Eight authored multi-file cases. Public entry points call through another module into `kernel.py::fold`; wording and distractors favor other files. Gold patches are outside the indexed repos. | All eight fail before and pass after the gold patch. BM25 retrieves no gold function at Top-3 or Top-10. These cases deliberately stress lexical mismatch; they are not a held-out representative sample or evidence that the full agent succeeds. |
| BugsInPy/PySnooper extension | Three candidate bugs rebuilt with Python 3.8, pytest 6.2.5, `python_toolbox` and `future`. | Two reproduce. The remaining buggy test already passes on this host and is excluded from the verified manifest. This small second real-project pilot broadens coverage without claiming three valid failures. |

The builder's annotation maps modified non-test lines to enclosing AST functions
and flags module-level edits. Some fixes span several functions, so retrieval
reports both “any gold function” and “all gold functions”; finding one function
does not establish a correct complete patch. The cases have no train/test split
or independent annotation review. Generated, controlled, and historical-project
results must remain separate. Reproduction counts are recorded in
[datasets.json](../../results/deep/datasets.json) and
[controlled/summary.json](../../results/deep/controlled/summary.json), with
per-case evidence retained alongside them.

The two primary comparison baselines use the team's shared chunking/retriever
and model conditions: `static_k3` retrieves once at Top-3 and diagnoses once;
`static_wide` retrieves once at Top-10 and diagnoses once. The wider baseline
tests whether simply supplying more context explains any advantage of adaptation.
The synthetic runner currently calls this baseline `static_wide`; the proposed
real-case factory calls it `static_k10`. Karthik must record the actual names,
prompts, budgets and model settings for the final matched experiment.

The stakeholder's [original implementation](https://github.com/YB-Yottabyte/adaptive-context/tree/c545608474b62e5635d240c49a790703e687848e)
is a separate third reference: Chroma, 80-line windows with 20-line overlap,
Top-3, and one `openai/gpt-oss-20b` call through Groq. The adapter raises the
planned output allowance from 512 to 4,096 tokens and records it. Its temporary
presentation hook saves the original response and selected windows, bypassing
terminal animation while retaining the original prompt/retrieval/provider call.
Complete AST-function coverage is required for grounding credit. Its chunking,
embeddings and prompt differ from the team's, so it is not a matched ablation.
**The live run was not performed: no Groq key was available.** All 32 planned
rows are `not_run`, and accuracy/usage are unmeasured, not zero.
[Recorded status](../../results/deep/stakeholder/summary.json).

The LLM-free evidence audit finds a gold function in BM25 Top-3 for 11/32 cases
and Top-10 for 19/32. Only 10/32 stored failing-test outputs explicitly name a
gold file's relative path; 28/32 test modules directly import a gold module.
This supports testing tool-driven inspection, without proving a model benefit.
The real BM25–Chroma comparison, including an observed duplicate-ID failure and
an explicitly labeled preprocessing variant, is in
[Deep's experiment notes](deep_experiments.md). These retrieval measurements are
not full-agent diagnosis or fix-correctness results.
