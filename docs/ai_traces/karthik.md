# AI Assistance Trace: Karthik Venkata Sai Reddy Kunduru (kkundur1)

This file discloses the AI assistance I used while working on the Capstone
project "Adaptive Context for LLM-Based Software Debugging" (Group 16). It is
provided for the course generative-AI disclosure policy. Any API keys or
passwords that appeared in my working session have been removed from this file.

## Tool used

AI coding assistant working inside my local editor, with access to my workspace
files and a terminal. I used it interactively across one long working session
leading up to the Milestone 2 deadline.

## What the AI helped me do

My area of the project is the evaluation harness, the MCP server, the caching
speed layer, and the assembly of the Milestone 2 report. Concretely, with AI
assistance I:

- Built and fixed evaluation-harness code and merged it into the team repo
  through reviewed pull requests:
  - `report.py` (results reporting)
  - `aggregate.py` and `contributions.py` (metrics aggregation; owner-name fix)
  - `baseline.py` fixes (stop_reason / wall_s fields; reject empty diagnosis)
  - `systems.py` (system factory)
  - `run_cases.py` (case runner)
  - `run_m2_eval.sh` (eval entry script)
- Built an MCP server (`mcp_server.py`) exposing `diagnose_bug` and
  `localize_fault`, resolving Python/FastMCP/pydantic version conflicts and
  verifying it end-to-end against a real MCP client (open PR).
- Built a caching speed layer (`bench/cache.py`, a CachingLLM wrapper) to cut
  repeated-run time, plus a Phase 4 work-division plan `docs/PHASE4_PLAN.md`
  (open PR).
- Ran real evaluation data on Groq (live model runs and a simulator), keeping
  live and simulator results clearly labeled and never mixed.
- Assembled the Milestone 2 report from the team's section drafts: wrote
  Section 1 from the real Nov 8 stakeholder meeting notes, built the Section 2
  architecture diagram (Graphviz), rendered the Section 4 fix-check figure from
  a real result file, and produced the styled PDF/DOCX.

## How I used it (honesty notes)

- Results in the report come from real runs. Where numbers are from the
  simulator rather than live model calls, they are labeled as such and never
  presented as live data.
- I declined to let the AI commit my teammates' work under my account. Each
  teammate owns and authors their own parts and their own AI trace file.
- API keys that appeared in my working session have been revoked and are not
  included anywhere in this file or the repository (the repo `.env` is
  gitignored).

## Secrets removed

No API keys, tokens, or passwords are included in this trace. Any key that was
pasted during the session has been revoked separately.
