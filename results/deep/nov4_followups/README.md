# Deep's follow-up validation — November 4, 2026

Source: committed revision `d75bc7c`, based on merged main `4d7c241`.
This includes PR #10's integration fixes and PR #11's real-case runner.
`verification.json` records commands, output files, timings and the clean source
state. `PYTHONPATH` selects this checkout's source for those commands.

Three implementation commits address separate evaluation concerns:

- `9bb5bc1`: Preserve an explicit `Function:` value, including class-qualified
  names, when suggested code includes a helper before the diagnosed function.
  Both regression cases fail on the previous parser and pass after correction.
- `944595e`: Validate response capture types and line windows. A malformed capture
  becomes a counted execution error and later cases continue. Four malformed-data
  regressions and a two-case continuation test fail before the correction.
- `d75bc7c`: Record the input manifest SHA-256 and each case's bug-report SHA-256,
  including skipped/error rows. A regression verifies changed reports change both
  fingerprints while preserving unmeasured statuses and null metrics.

| Evidence | Observed result |
| --- | --- |
| `pytest.txt`, `ruff.txt` | All 163 tests pass; lint passes. |
| `audit/` | All 32 original reports processed without errors; BM25 gold recall stays 11/32 at Top-3 and 19/32 at Top-10. |
| `controlled/` | All eight controlled fixtures fail before and pass after their gold patch. |
| `fixcheck/` | All 32 maintainer fixes pass, all 32 unchanged-body controls are rejected, and all 32 buggy regression tests fail. |
| `stakeholder/` | All 32 rows are explicitly `not_run`, with manifest/report fingerprints. Measured count is zero and metrics remain null. |
| `installed_cli.txt`, `verification.json` | The installed package matches all 33 Python source files, and a separate one-case smoke check loads it without `PYTHONPATH` and records the new fingerprints. |

No live provider call was made. Native Chroma's existing duplicate-ID limitation
still requires Yadnesh's production fix. The new tests use saved/synthetic output
and an offline child-process fixture; they do not establish live model accuracy.
Original experiment artifacts are preserved. Reruns should use `results/local/`
instead of overwriting this evidence directory.
