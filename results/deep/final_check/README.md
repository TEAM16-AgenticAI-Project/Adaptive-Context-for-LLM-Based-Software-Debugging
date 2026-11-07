# Final command checks — November 1, 2026

These checks use committed source `9af7cc9f1ba2f55cabf594470ddf49aa0bddc4ef`,
which incorporates main `dc5eafd`. Source files were clean when checked.
`verification.json` records each command, exit code, elapsed time and output
file. The regular installation used by the CLI was also compared byte for byte
with the checkout's Python source; the comparison is recorded there.

| Evidence | Observed result |
| --- | --- |
| `pytest.txt`, `ruff.txt` | 151 tests pass; lint passes. |
| `audit/` | All 32 original reports processed without errors; BM25 finds a gold function in 11/32 Top-3 and 19/32 Top-10 results. |
| `controlled/` | All eight checked-in cases fail before their gold patch and pass after it. |
| `fixcheck/` | All 32 maintainer fixes pass, all 32 negative controls are rejected, and all 32 buggy regression tests fail. |
| `cli_help.txt`, `stakeholder_help.txt`, `dataset_help.txt` | Installed command entry points load successfully. |

The first command preflight exposed a local installation problem: macOS
`UF_HIDDEN` flags caused Python 3.12 to skip the virtualenv's `.pth` files.
Pytest still found the package through its configured source path, so passing
tests alone did not establish that installed commands worked. Clearing the flags
only helped temporarily in this environment. Installing the package regularly
instead of editably avoided that dependency on `.pth` processing:

```bash
uv pip install --python .venv/bin/python --reinstall-package adaptive-context '.[dev,agent]'
```

No application source change was required for this local repair. Reinstall after
source changes when using a regular installation. Use the explicit manifest and
output arguments recorded in `verification.json`; the handoff contains the
portable development setup and source pins for rebuilding ignored real repos.

These are offline evidence and test checks. No Groq call was made, the native
Chroma duplicate-ID limitation remains documented separately, and no stakeholder
meeting or final group submission is claimed. The original experiments and their
producing revisions remain unchanged.
