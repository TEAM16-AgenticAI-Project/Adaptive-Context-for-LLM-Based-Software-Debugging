# Integration recheck — November 1, 2026

The branch now includes main `963f56a` (Tanay's verification PR). The shared
audit CLI retains both `--audit` and `--validate-fixcheck` and both published
summary-helper forms. Conflicting task disclosures were combined without
removing either author's entry.

Corrections found during review:

- Preserve `**` in Python fixes while parsing bold summary headings.
- Score a repeated chunk ID at its first retrieved rank.
- Normalize interpreter paths without dereferencing virtualenv symlinks.
- Retain both audit/verification actions and update obsolete placeholder tests.
- Test response capture through an actual offline child process with a fake
  provider, not just a mocked subprocess call.
- Refresh stale README implementation claims and Tanay's contribution links.

Measured checks are stored here, separately from the original experiments:

| Files | Check |
| --- | --- |
| `audit/` | Original 32 reports still produce BM25 gold hits 11/32 at Top-3 and 19/32 at Top-10, without audit errors. |
| `controlled/` | All eight actual checked-in controlled cases fail before and pass after their gold patch. |
| `fixcheck/` | Corrected integrated CLI: all 32 maintainer fixes pass, all 32 unchanged-body controls are rejected, and all 32 buggy repositories fail their regression tests. |
| `fixcheck_interpreter_failure/` | Preserved failed preflight: dereferencing virtualenv Python selected an interpreter without pytest. These are setup failures, not model-accuracy measurements. |
| `clean_checkout.json`, `clean_checkout_pytest.txt` | A fresh Python 3.12 `.[dev,agent]` installation from `git archive HEAD`; return code and output are recorded verbatim. |
| `pytest.txt`, `ruff.txt` | Local integration gate; the clean-check output is the final gate including the virtualenv regression. |

Each experiment records its producing code revision. The original retrieval
experiment files remain unchanged. A clean checkout intentionally lacks ignored
third-party exports and credentials; those must be rebuilt/configured for their
respective optional commands. No Groq call was performed. The original Chroma
duplicate-ID limitation and the separate preprocessing experiment remain
explicitly documented; this review does not silently change production
chunk-identity policy.
