# Final adapter checks — November 8, 2026

Producing source revision: `ce3f0b1`, based on merged main `044ebd8`.
PR #13's prior adapter corrections and PR #12's evaluation script are included.

Two implementation commits address separate failures:

- `9760c2d`: A single method in a class-wrapped suggested patch now yields the
  class-qualified function name. Regular and async methods are covered, and
  multiple-method replacements retain the module-level fallback. The regular and
  async method regressions fail before the correction.
- `ce3f0b1`: Invalid captures and process-launch failures retain redacted error
  details, available return codes and process logs while remaining errors with
  null metrics. Both diagnostics regressions fail before the correction.

`verification.json` records the committed revision, clean source state, commands,
exit codes and output paths. `pytest.txt` records all 171 tests passing, including
five new regression cases; `ruff.txt` records passing lint.

`stakeholder/` records an explicit offline check on all 32 original reports:
32 `not_run` rows, zero measured executions, null scores and input fingerprints.
This check uses `--skip-live`; no live Groq call or model accuracy is claimed.
The parser fixtures and injected process failures are synthetic test evidence.

Earlier dataset reproduction, gold-fix validation and BM25/Chroma experiments
retain their own producing revisions and outputs. Native Chroma's previously
recorded duplicate-ID limitation still requires Yadnesh's production follow-up.
Keep reruns under `results/local/` rather than overwriting published evidence.
