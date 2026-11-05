# Deep — Milestone 2 handoff

The original implementation on `deep/datasets-baselines` was merged in
[PR #7](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/7).
Integration fixes and verification were merged on November 3 through
[PR #10](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/10).
The current follow-up branch is `deep/m2-evidence-followups`, based on main
`4d7c241`, including Karthik's real-case runner from PR #11.
This contribution covers Deep's assigned area in `docs/m2_tasks/deep.md`.

The [integration recheck](../../results/deep/recheck/README.md) records offline
validation after merging Tanay's main-branch changes, including both
shared CLI actions and the virtualenv interpreter regression.
The [November 1 command checks](../../results/deep/final_check/README.md) run both
actions and the controlled-case validator on their recorded source revision.
The [November 4 follow-up checks](../../results/deep/nov4_followups/README.md)
record validation of the current adapter fixes against the newer merged main.

## Completed

- LLM-free evidence audit for every dataset-B case: full BM25 gold ranks,
  Top-3/10 any/all recall, traceback filename checks, parsed test imports and
  function-name ambiguity. Rows and summary include revision/input hashes.
- Stakeholder CLI adapter with a saved-output parser, original-response/window
  capture, the larger recorded output allowance, error/timeout handling and
  explicit `not_run` behavior. It copies only the buggy repo and report; gold and
  source metadata remain in the evaluator. `.env` is read as data for live use.
  Follow-up fixes preserve explicit function names when a suggested patch also
  defines helpers, reject malformed capture payloads without aborting the batch,
  and record manifest/report hashes for future evaluation comparisons.
  Timeout failures also retain redacted partial stdout/stderr, including byte
  output, while keeping their status as errors and their metrics null.
  [Timeout regression evidence](../../results/deep/nov4_timeout/README.md)
  records the committed implementation and subsequent checks.
- Eight controlled multi-file fixtures, manifests and gold patches outside the
  indexed repos. Validation demonstrates actual test failure before and passing
  after each gold patch without changing the original buggy files.
- Fresh reconstruction of all thefuck cases and a PySnooper pilot, retaining all
  unsuccessful attempts and a separate verified-only manifest.
- Chroma adapter tests and two actual BM25–Chroma experiments. The first exposed
  duplicate-ID errors; the second is an explicitly labeled shared-input variant
  retaining the last definition per AST ID for both rankers. Production chunker
  and retriever code were not changed.
- Report drafts: [Section 3.1](section3_1_datasets_baselines.md),
  [Section 6](section6_remaining_work.md), [Section 7](section7_contributions.md).
  [Experiment analysis](deep_experiments.md) is separate supporting material for
  Karthik, not another full group report.

The main-snapshot linter already failed on one unused import in Karthik's
`bench/contributions.py`. This branch removes that import only, with no behavior
change, so the required repository-wide check passes. All other implementation
changes are Deep's new tooling, datasets, tests and report files; existing shared
interfaces are preserved.

## Evidence and limits

See [results/deep](../../results/deep/README.md) for the artifact index and producing
revisions. Reproduction, retrieval, model diagnosis and gold-fix validation are
distinct measures. No live Groq call was made because no key was available, as
agreed during this work. The third-reference file contains planned `not_run`
rows and null metrics; it is not a zero-accuracy model result. The whole-system
comparison, final integrated demonstration, stakeholder meeting and group report
assembly still belong to the team owners.

The native Chroma failure must be reviewed with Yadnesh before presenting the
last-definition variant as production behavior. Main `4d7c241` is now integrated,
including Karthik's baseline fixes, systems factory and real-case runner:
`bench/audit.py` supports both `--audit` and Tanay's `--validate-fixcheck`, preserving
both helper/result interfaces. The verifier also accepts `--repo-root`, and the
shared CLI makes interpreter paths absolute without dereferencing virtualenv
symlinks before running temporary copies.
Tanay's handle is `Tanay126`; final non-code contributions still need his confirmation.

The integration recheck also preserves Python `**` operators while parsing bold
response headings and scores duplicate hits at their first rank. The stakeholder
capture hook is tested through a real offline child process using a fake provider;
this is an integration test, not a live Groq result.

## Quick checks

From an installed development environment:

```bash
pytest -q
ruff check src tests
python -m adaptive_context.bench.controlled --out results/local/deep/controlled
python -m adaptive_context.bench.audit --audit --manifest data/controlled/cases.jsonl --out results/local/deep/controlled_audit
```

For a fresh checkout, install `.[dev,agent,chroma]` using Python 3.12 and follow
`scripts/setup_env.sh` for the Python 3.8 environments/project checkouts. That
script regenerates the supplied real manifest, including its traceback text.
Keep reruns under `results/local/` so the published evidence is preserved.
On this Mac, hidden flags on virtualenv `.pth` files prevented Python from
loading the editable package, although pytest's configured source path worked.
A regular installation restored the commands:
`uv pip install --python .venv/bin/python --reinstall-package adaptive-context '.[dev,agent]'`.
When using this fallback, reinstall after source changes and supply explicit
manifest/output paths to the commands below.
**Preserve the original report text before setup** for exact query comparisons:

```bash
cp data/real/thefuck.jsonl /tmp/team16-thefuck-inputs.jsonl
# Now run the existing setup script using the development interpreter.
python -m adaptive_context.bench.audit --audit --manifest /tmp/team16-thefuck-inputs.jsonl --repo-root data/real --out results/local/deep/audit
python -m adaptive_context.bench.audit --audit --chroma --manifest /tmp/team16-thefuck-inputs.jsonl --repo-root data/real --out results/local/deep/retrievers
python -m adaptive_context.bench.audit --audit --chroma --deduplicate-chunks --manifest /tmp/team16-thefuck-inputs.jsonl --repo-root data/real --out results/local/deep/retrievers_deduplicated
```

The original Chroma command is expected to exit nonzero on the affected
snapshots, with its errors saved. To use the fresh exports retained by this
branch, pass `--repo-root data/reproduced` instead; `--repo-root` resolves only
checkout paths and does not rewrite the original report or gold metadata.
Audits use the original manifest hash, while the dataset summary records the
fresh reproduction manifest separately.

The exported third-party repos are not committed. Rebuild using the existing
builder with the source pins in `results/deep/datasets.json`. For thefuck use
bugs 1–32, `--env-for 3-21=py38-pytest3`, default `py38-pytest6`; use absolute
interpreter paths. [PySnooper instructions](../../data/pysnooper/README.md) describe
the two extra test dependencies. Do not run untrusted project code merely because
the runner uses a temporary copy; the temporary directory is not a security sandbox.

## Running the deferred live reference

Clone [the stakeholder repository](https://github.com/YB-Yottabyte/adaptive-context)
outside the team repo and check out reviewed commit
`c545608474b62e5635d240c49a790703e687848e`. Its original environment is resolved by
`uv run`, so install `uv` and allow its dependencies/model to download. Add
`GROQ_API_KEY` to the team checkout's ignored `.env` or export it; do not commit it.

```bash
python -m adaptive_context.bench.stakeholder_baseline --baseline-root /absolute/path/to/stakeholder --manifest /tmp/team16-thefuck-inputs.jsonl --repo-root data/real --limit 1 --max-tokens 4096 --out results/deep/stakeholder_smoke
# Review the raw response and parser result before the full dataset run.
python -m adaptive_context.bench.stakeholder_baseline --baseline-root /absolute/path/to/stakeholder --manifest /tmp/team16-thefuck-inputs.jsonl --repo-root data/real --max-tokens 4096 --sleep 3 --out results/deep/stakeholder_live
```

`--skip-live` writes only explicit status rows without starting the upstream
process. Live failures count against executed-case accuracy; unexecuted rows
remain outside that denominator. No measured usage is invented when upstream
does not provide it. Review raw outputs for truncation or incomplete diagnoses.
Full-function grounding is conservative because an 80-line window can truncate
a longer function.

Keep the full conversation/export as the course interaction trace. Deep's
disclosure is appended to `docs/AI_ASSISTANCE.md`. The current follow-up branch
needs a teammate's review and merge, followed by the team's final evaluation on
merged main and one final submission.
