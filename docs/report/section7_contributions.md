# Section 7 — Repository and team contributions (Deep draft)

Repository: [TEAM16-AgenticAI-Project / Adaptive-Context-for-LLM-Based-Software-Debugging](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging).
Deep's original contribution on `deep/datasets-baselines` was merged through
[PR #7](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/7).
Follow-up integration fixes and verification are on `deep/verification-recheck`
in [PR #10](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/10),
incorporating main `dc5eafd` (Tanay's verifier and Karthik's baseline/factory changes).
Experiment files record their producing commits individually; the
group must record the final merged submission commit after review and the final
evaluation. The repository was public when checked on November 1, 2026.

| Team member / GitHub | Contributions evidenced by code and artifacts | Representative commits / PRs | Non-code contribution and status |
| --- | --- | --- | --- |
| Deep Nayak / `Deep-nayak007` | Evidence audit; controlled-case builder/validation; reproduction manifests/summary; stakeholder-baseline adapter; Chroma tests and matched-input retrieval comparison. | [Audit/cases](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/d13d4e5), [baseline adapter](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/b8e5ec0), [duplicate-definition variant](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/1124a2a). | Reproduced dataset B and extension; analyzed retrieval failures; drafted Sections 3.1, 6 and 7. Live Groq reference remains unrun. |
| Prem Mahendra Pagare / `prempagare07` | Shared case/test plumbing and task interfaces; LangGraph agent with native tool calling. | [Setup](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/3218442), [agent](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/83e88d8), [PR #5](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/5). | Stakeholder coordination and Sections 1–2 assigned; actual meeting/writing details need his confirmation. |
| Yadnesh Deshpande / `yashpy` | Initial context-management system; import-aware lookup, budget/pinning/compression changes; ablation and ambiguity tooling; failure-analysis draft. | [Initial system](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/6ffa0c0), [import resolution](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/17a04fd), [PR #3](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/3). | Context experiments and Section 5 failure-1 work supported by the associated scripts/draft. |
| Karthik Venkata Sai Reddy Kunduru / `kkundur1` | Benchmark aggregation, Markdown/JSON results reporting and history-based contribution generator. | [Results reporting](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/a3eb2bb), [aggregation](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/75daf83), [PR #4](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/4). | Full matched evaluation, Section 3 and final report assembly assigned; distinguish the final runs from earlier simulator tables. |
| Tanay Parikh / `Tanay126` | Real-repository fix checker, maintainer/negative-control validation, integrated agent verification test, case study and failure-analysis draft. | [Fix checker](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/f12d56f), [validation CLI](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/ea5365d), [PR #6](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/6). | Validation artifacts and Sections 4/5 are merged. Confirm final slide and other non-code contributions with Tanay. |

The table describes work rather than using commit counts as a proxy for effort.
[The generated history inventory](../../results/deep/contributions.md) supports
the commit evidence but cannot verify meetings or other offline work. Stakeholder
Sai Rithwik Kukunuri is a different person from Karthik; their names must not be
merged. All members should review this draft before submission. Deep's AI-use
disclosure is appended in [AI_ASSISTANCE.md](../AI_ASSISTANCE.md); retain the full
interaction trace for the course submission.
