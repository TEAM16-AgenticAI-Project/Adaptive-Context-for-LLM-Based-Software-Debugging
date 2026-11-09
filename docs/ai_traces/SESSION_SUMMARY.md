# Session summary: context manager, results, web UI and deployment

Project: *Adaptive Context for LLM-Based Software Debugging* (CSE 598 capstone, Milestone 2, due Nov 8, 2026).
Owner of this work: Yadnesh Deshpande.
Team repo: https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging

This file records what was done in the session with an AI pair-programmer, in order. It is a
summary, not the full chat transcript. The course asks for the full interaction trace, so keep
the chat export as well.

---

## 1. Context-manager task (code-review findings 2 to 8)

### Starting point
- Read `docs/m2_tasks/README.md`, `docs/m2_tasks/yadnesh.md`, `docs/CODE_REVIEW.md`, `models.py`,
  `cases.py`, `tools.py` and the six owned files: `budget.py`, `compress.py`, `sequencing.py`,
  `chunking.py`, `retrieval.py`, `adaptive.py`.
- `main` had 19 passing tests; ruff was clean.

### Decisions
- **Budget meaning:** `token_budget` is the whole prompt. Code gets what is left after the system
  prompt and bug report (`code_budget(total, *prompt_parts, floor=200)`), as the task said.
  Whether the "600 no worse than 800 today" goal compares against the old code-only 800 was left
  for review.
- Stop reasons: `diagnosed`, `forced_diagnosis`, `round_limit`, `invalid_exhausted`, `llm_error`.
- Notes are a fifth compression level `note` in `compress.LEVELS`, so `protocol.py` needed no edit.
- New tests went in `tests/test_context_manager.py` to avoid conflicts in `tests/test_core.py`.
- Git identity for commits: `Yadnesh Deshpande <yadnesh.deshpande1@gmail.com>`, no co-author lines.

### Commits on `yadnesh/context-manager` (sandbox, first version)
1. chunking: split module-level code into bounded chunks without truncation; ids `path::<module>`
   and `path::<module>@L<n>`; record dotted calls; silence `SyntaxWarning`.
2. retrieval: lookup forms resolved most-specific-first (chunk id, pytest id, dotted module path,
   `Class.method`, short name), never a union.
3. retrieval: import-aware callees (same file, then imports, then name match).
4. budget: `code_budget`, pinned chunks kept at `trimmed` or better, unpinned dropped first, oldest
   pinned dropped first, `PackResult.over_budget` and `dropped_pinned`.
5. compress/budget: eviction notes (`pack(..., use_notes=True)`).
6. adaptive: whole-prompt budget, pinned recency score, `stop_reason`, `wall_s`,
   `over_budget_rounds`, `ValueError` on bad arguments, `pin_policy` (legacy / pinned / pinned_notes).
7. bench: `bench/budget_ablation.py` and `bench/name_ambiguity.py`.
8. docs: `docs/AI_ASSISTANCE.md` and the Section 5 failure 1 draft.
9. docs: ablation table refreshed with the N=300 run.

Tests: 50 passed, ruff clean.

### Simulator ablation (dataset A, 300 cases per cell, 60 modules, 40 chains)
| total budget | legacy | pinned | pinned + notes |
|---:|---:|---:|---:|
| 600 | 0.597 | 0.887 | 0.887 |
| 800 | 0.980 | 0.980 | 0.980 |
| 1000+ | 1.000 | 1.000 | 1.000 |

- Evidence recall at 600: 0.837 to 0.997.
- The "600 with notes no worse than 800 today" goal was **not** met on the simulator.
- Notes do not separate from plain pinning on the simulator: it only verifies code at level 2 or
  better, and the scorer ignores level-4 notes.

Real trace used in the Section 5 draft: generated seed 1, depth-4 bug. Legacy packing left the
buggy function at `signature` and diagnosed the entry function; pinned packing kept it at
`trimmed` and found `apply_gross_margin`.

The name-ambiguity statistic on dataset B needs `data/real/repos/` (built by `setup_env.sh`), so it
was only checked on a synthetic repo (3 candidates for `match` before, 1 after).

### Pushing problems
- Pushing from the sandbox to the team repo returned **HTTP 403** for both `git push` and the REST
  API, even though the account (`yashpy`) has admin access. Reads worked; writes were blocked by
  the sandbox's GitHub gateway.
- Tried: a git bundle, base64 bundle in three parts, patch series. None was convenient.
- The context-manager work later reached `main` through PR #3 (your own push).

---

## 2. Understanding and running the project

- `python -m adaptive_context.bench.run --n 100 --workers 4 --miss 0.1` (simulator):
  static_k3 0.20, static_wide 0.24, adaptive 0.88, adaptive_graph 0.92 function accuracy.
  By depth, the static baseline is 0.00 once the bug is 2 or more calls deep.
- `python -m adaptive_context.cli debug examples/payment_bug ... --mode adaptive --llm sim`
  retrieves the right three chunks but gives no diagnosis: the simulator only recognises the
  generated docstring-vs-return bug pattern. Groq mode is needed for real code.
- Committed results on `main` live in `results/` and the report write-ups in `docs/report/`
  (your Section 5 failure 1 is `docs/report/section5_failure1.md`).

---

## 3. First web UI (adaptive-context-ui)

Built a FastAPI backend plus a no-build HTML/CSS/JS frontend, with Dockerfile,
docker-compose, `.env.example`, Makefile, README and 5 smoke tests. Pushed to
`yashpy/Resumes`, branch `adaptive-context-ui`, folder `adaptive-context-ui/` (writes to that
repo worked; creating a new repo returned 403).

Added later on the same branch:
- `render.yaml` (Render Blueprint, free plan) and `DEPLOY.md`.
- `sample-bugs/cart_app`: a 4-file cart bug where `tax.py::apply_tax` ignores the discount
  (`cart_total([(100.0, 1)], 0.20, 0.10)` returns 110.0 instead of 88.0), plus `cart_app.zip` and
  `TRY_YOUR_OWN_BUG.md`.

### Deployment issues you hit
1. **`Root directory "adaptive-context-ui" does not exist`.** You had pushed the unzipped app to
   the team repo's root on branch `UI-changes`, while `render.yaml` and the Render service still
   pointed at the subfolder.
2. **`local path access is disabled on this server`.** The "Bundled example" tab sent a server path,
   which is blocked when `ALLOW_LOCAL_PATH=0` (the deployed default).

---

## 4. Rebuilt UI on `UI-changes` (merged with `main`)

### Review of `main` first
- Merged PRs #7 to #14 (Deep, Karthik, you) only changed `bench/` evaluation scripts. All 18
  engine files were identical to the UI's copy.
- The old `UI-changes` branch had **no common history with `main`**, so no PR was possible.
- Failing in a deployment: Bundled example and Server path tabs (403), git URLs without `.git`
  (400), Groq without a key (503), and empty simulator results that looked broken.

### Commits (all authored by Yadnesh Deshpande)
| Commit | Change |
|---|---|
| `bf66ef1` | Merge `main` into `UI-changes`; UI moved under `ui/`; vendored engine copy removed |
| `ea0c275` | Backend runs on `src/adaptive_context`; all systems (static_k3, static_k10, adaptive, adaptive_graph, LangGraph agent); sources: examples, generated cases, zip upload, git URL, server path; scoring after the run; optional fix check; rate limit, concurrency cap, upload checks, security headers |
| `0a88c61` | Frontend rebuilt: comparison table, evidence viewer with compression level and the buggy function marked, round-by-round trace with tool calls, How it works page, JSON download |
| `ff8b790` | 18 UI tests |
| `6234323` | Root `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `render.yaml` (no `rootDir`), Makefile |
| `5ae42d7` | `ui/README.md` (API, settings, security, free deploy); short sections in `README.md` and `.env.example` |

Checks: `main`'s 171 tests and the 18 UI tests passed; `ruff check src tests ui` clean; the page
was loaded in a headless browser and a generated case run end to end (static Top-3 wrong,
adaptive, adaptive + call graph and the agent correct with a passing fix check).
Not tested: the Docker build itself (the container tool did not work in the sandbox) and Groq mode
(no API key).

Pushed to `yashpy/Resumes`, branch `UI-changes`, because the team repo still returned 403.

### Where to edit the UI
| Change | File |
|---|---|
| Layout and text | `ui/frontend/index.html` |
| Behaviour and result display | `ui/frontend/app.js` |
| Colours and spacing | `ui/frontend/style.css` |
| API, examples, limits, systems | `ui/backend/main.py` |

Leave `src/`, `data/controlled/`, `examples/payment_bug/`, `Dockerfile` and `render.yaml` in place;
the UI and the deploy depend on them.

---

## 5. Moving the branch to the team repo

- The downloaded ZIP folder (`Resumes-UI-changes`) was not a git repository, so `git remote add`
  failed. Running `git init` there and committing produced a branch with no history.
- That became `UI-changes2` (commit `46df0ab`). Compared with `5ae42d7`, it has **no edits**; it is
  missing the `results/` folder (92 files, excluded by `.gitignore`) and has no history.
- The Render error persists because the service setting **Root Directory = `adaptive-context-ui`**
  is still saved.

The fix steps are in `UI_CHANGES_FIX.md` (same folder as this file). In short:

```bash
# from the Resumes-UI-changes folder (origin = team repo)
git remote add resumes https://github.com/yashpy/Resumes.git
git fetch resumes UI-changes
git push origin resumes/UI-changes:UI-changes      # normal push, no --force
```

Then in Render: Branch `UI-changes`, Root Directory empty, Dockerfile Path `./Dockerfile`, Build
Context `.`, Health Check `/api/health`, then **Clear build cache & deploy**. Delete `UI-changes2`
once the deploy works.

---

## 6. Open items
- Push `yashpy/Resumes:UI-changes` to the team repo's `UI-changes` and fix the Render settings.
- Optionally open a PR from `UI-changes` into `main`.
- Run `bench/name_ambiguity.py` on the real repos for the dataset-B number.
- Add a Groq key on Render to get real diagnoses on non-generated code.
- `docs/AI_ASSISTANCE.md` on `main` already discloses AI use in your line; update it if needed.
- Keep the full chat export as the interaction trace for submission.
