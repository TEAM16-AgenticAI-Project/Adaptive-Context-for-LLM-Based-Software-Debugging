# Timeout diagnostics follow-up — November 4, 2026

Source revision: `a0ac9cc`. Timed-out stakeholder executions previously lost
their partial stdout/stderr. The adapter now decodes byte output safely, redacts
the configured key, and retains those logs. A timeout continues to be an error
with null metrics; partial output is never scored as a successful diagnosis.

Three regression cases cover text output, byte output with invalid UTF-8, and
missing output. They fail before the fix because the diagnostic fields are
absent. The tests inject synthetic timeout exceptions and use a fake key; they
do not start a live model call.

`verification.json` records the producing commit, clean source state, commands,
exit codes and output paths. `pytest.txt` records all 166 tests passing, and
`ruff.txt` records the passing lint check. Earlier dataset, retrieval and
gold-fix experiments retain their own producing revisions and were not replaced
by these unit-test results.
