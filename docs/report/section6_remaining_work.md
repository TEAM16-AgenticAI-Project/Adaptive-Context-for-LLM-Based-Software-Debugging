# Section 6 — Remaining work (Deep)

Required work takes priority over extensions. Owners follow the team's task
split; completion below means recorded evidence, rather than a planned feature.

| Priority / current gap | Improvement or experiment | Owner | Done when |
| --- | --- | --- | --- |
| Required: stakeholder feedback not documented in this checkout | Review the running system and preliminary results with Sai Rithwik Kukunuri; confirm scope and success criteria. | Prem leads; all members participate. | Dated participants/notes and Feedback → Team response → Next action table; agreed final deliverables. |
| Required: no current real-case results across all systems | Finish the case runner/system factory; run the same cases through both static baselines and agent variants with matched configurations. | Karthik; Prem and Tanay integrate their components. | Saved per-case diagnoses, traces, metrics, budgets, uncertainty and failures; final results regenerated from merged main on both required datasets. |
| Required: stakeholder-reference live run unavailable | A teammate supplies a Groq key and runs Deep's adapter on all dataset-B cases. Inspect truncation, parsing and provider failures. | Deep + Karthik. | Actual outputs/usage and scores replace `not_run` rows; no key committed. |
| Required: default Chroma rejects repeated AST IDs | Agree a chunk-identity/shadowing policy. Keep Deep's last-definition comparison variant explicitly separate until the production path is corrected. | Yadnesh owns chunking/retrieval; Deep provides the reproducer and reruns the comparison. | Native indexing works for every intended snapshot; regression test and new results record the chosen policy. |
| Required: verification and diagnosis claims need execution evidence | Merge `check_fix`, validate gold fixes and negative controls, then verify actual model fixes in the real-case runner. | Tanay; Prem/Karthik wire it into agent/evaluation. | Saved application/test outcomes, original repos unchanged, unsupported fixes/errors counted honestly. |
| Required: dataset and label review | Review multi-function/module-level labels; retain PySnooper's unsuccessful attempt; evaluate the verified extension and controlled cases with the completed systems. | Deep + Karthik; team reviews annotations. | Reviewed labels and manifest hashes, explicit evaluated sample counts; no reuse of maintainer patches in prompts. |
| Required: submission and demo assembly | Add real agent/tool/fix-check screenshots, reconcile with the group Milestone 1 report, update stale README status, confirm contributions and freeze the final commit. | Prem: Sections 1–2/demo; Tanay: verification/slides; Yadnesh: failure analysis; Deep: Sections 3.1/6/7; Karthik: evaluation/report assembly. | One 5–10 page template-based report, current code/commit and access instructions, reproducible results, presentation and actual demonstration. |
| Optional | Expand real projects, embedding models, budgets, and hard failure cases after required evaluation is complete. | Whole group. | A measured benefit or informative failure, with the same reporting and evidence standards. |

Real-case execution uses temporary copies, selected interpreters and timeouts;
it is not an isolation sandbox. Simulator tables, actual model tables,
retrieval-only measurements and gold-patch validation must be distinguished in
the final report.
