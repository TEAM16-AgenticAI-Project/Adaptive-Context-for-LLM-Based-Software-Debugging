# Section 1 - Stakeholder Follow-Up and Final Deliverables (Prem)

**Contacts.** November 3, 2026, written feedback from the stakeholder on the multi-file work. November 8, 2026,
Milestone 2 review over Zoom. Stakeholder: Sai Rithwik Kukunuri (organizer). Participants: Sai Rithwik Kukunuri,
Prem Mahendra Pagare, Yadnesh Deshpande, Deep Nayak, Karthik Venkata Sai Reddy Kunduru, Tanay Amit Parikh. As a
student stakeholder (not a professor or TA), Sai does not influence grading, but reviews the project against the
milestone requirements and the stakeholder proposal.

**What we presented (November 8).** The move from the Milestone 1 single-shot Static RAG baseline to the adaptive
loop and the LangGraph tool-calling agent (bounded rounds, context managed under a token budget, and a fix check
that reruns the failing test); new test cases, including the stakeholder's `auth_bug`; multi-file debugging, where
one fix changes several files and is checked as a whole; early evaluation work (datasets, controlled cases, evidence
audit); and a live demonstration on a bug.

**Feedback -> Team response -> Next action.**

| Feedback (date) | Team response | Next action / status |
| --- | --- | --- |
| Show that the agent does not repeat the same search (Nov 3). | A repeated tool call is detected and not run again, and overlapping searches add only new code. Unit test: `test_overlapping_searches_add_only_new_code_and_repeats_are_skipped`. | Done, in the pull request from `prem/agent-fixes`. |
| Test on a small real-world repository with a multi-file bug, starting from the `payment_bug` and `auth_bug` cases (Nov 3). | `auth_bug` was added from Sai's repository next to `payment_bug` (`data/stakeholder/cases.jsonl`); every system's fix passes on both. For real multi-file bugs we ran BugsInPy thefuck-16 and thefuck-17. With gpt-oss-20b the agent found the right files but its fixes did not pass all tests; with gpt-oss-120b it fixed thefuck-16 across `bash.py` and `zsh.py`, passing the failing test and the related tests. | Partly met; more multi-file bugs in Phase 3 (open items below). |
| Meet all Milestone 2 requirements and include the generative-AI trace where AI was used (Nov 8). | We mapped the report to the seven rubric sections and include the AI-use disclosure (`docs/AI_ASSISTANCE.md`) and the trace. | Finalize against every rubric item; Sai reviews before submission. |
| Write the report in a shared document and avoid pasting AI-generated content directly; AI may be used with disclosure (Nov 8). | Drafted material is treated as source to adapt, with AI assistance disclosed. | Yadnesh finalizes the report and sends it to Sai. |
| Share the current code, the metrics and the baseline comparison (Nov 8). | Prem ran the live comparison: two static baselines, the adaptive loop and the agent with fix check, on the same 8 bugs with the same model and the same fix check (Section 3.4). Code is in the pull request; the file is `results/m2/compare/comparison.md`. | Prem sends the comparison to Sai with this report. |
| A prior grade concern should go to the grader, since the stakeholder does not control grading (Nov 8). | We will raise it with the grader directly. | Deep emails the grader (Vedant) and awaits a response. |
| Include code comments and prepare for Phase 3 (Deploy and Audit) (Nov 8). | We will add code comments and plan the Phase 3 deliverables. | Team adds comments; Sai confirms stakeholder responsibilities for the acceptance test. |

**Not yet met.**

- At least three solved multi-file bugs (stakeholder requirement): one so far, thefuck-16, and only with
  gpt-oss-120b. With gpt-oss-20b neither multi-file bug in the comparison passed.
- The live comparison covers 8 bugs (the 2 stakeholder cases and 6 of the 32 real thefuck bugs). The rest is limited
  by the Groq free tier's daily token cap; the agent uses about 25k tokens per real bug.
- The stakeholder's Chroma reference has not been run live (Section 6).
- A fix that passes the tests is not proven correct (Section 5.2); the root-cause audit is still to do.
- The UI is deferred to the final phase and is not part of this report.

**Final deliverables and scope agreed.** Sai was satisfied with the direction and confirmed the Milestone 2 scope:
a working adaptive agentic debugger evaluated against the static baselines, with preliminary results, a
demonstration, a failure analysis and documented remaining work, in one group report that meets every rubric
requirement and includes the GenAI trace. Success is a correct, evidence-grounded diagnosis with focused context and
a fix that passes the bug's failing test and related tests, judged against the static baselines on the shared cases
(Sections 3-4). The agreed next phase is Phase 3 (Deploy and Audit). (Meeting notes are from the Zoom AI summary of
the November 8 session, reviewed by the team for accuracy.)
