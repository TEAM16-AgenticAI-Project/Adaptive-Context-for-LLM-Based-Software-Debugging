# Section 7 — Repository and team contributions (Deep draft)

Repository: [TEAM16-AgenticAI-Project / Adaptive-Context-for-LLM-Based-Software-Debugging](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging).
Deep's contribution branch is `deep/datasets-baselines`, based on main
`32b46e9`. Experiment files record their producing commits individually; the
group must record the final merged submission commit after review and the final
evaluation. The repository was public when checked on November 1, 2026.

| Team member / GitHub | Contributions evidenced by code and artifacts | Representative commits / PRs | Non-code contribution and status |
| --- | --- | --- | --- |
| Deep Nayak / `Deep-nayak007` | Evidence audit; controlled-case builder/validation; reproduction manifests/summary; stakeholder-baseline adapter; Chroma tests and matched-input retrieval comparison. | [Audit/cases](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/d13d4e5), [baseline adapter](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/b8e5ec0), [duplicate-definition variant](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/1124a2a). | Reproduced dataset B and extension; analyzed retrieval failures; drafted Sections 3.1, 6 and 7. Live Groq reference remains unrun. |
| Prem Mahendra Pagare / `prempagare07` | Shared case/test plumbing and task interfaces; LangGraph agent with native tool calling. | [Setup](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/3218442), [agent](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/83e88d8), [PR #5](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/5). | Stakeholder coordination and Sections 1–2 assigned; actual meeting/writing details need his confirmation. |
| Yadnesh Deshpande / `yashpy` | Initial context-management system; import-aware lookup, budget/pinning/compression changes; ablation and ambiguity tooling; failure-analysis draft. | [Initial system](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/6ffa0c0), [import resolution](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/17a04fd), [PR #3](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/3). | Context experiments and Section 5 failure-1 work supported by the associated scripts/draft. |
| Karthik Venkata Sai Reddy Kunduru / `kkundur1` | Benchmark aggregation, Markdown/JSON results reporting and history-based contribution generator. | [Results reporting](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/a3eb2bb), [aggregation](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/commit/75daf83), [PR #4](https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging/pull/4). | Full matched evaluation, Section 3 and final report assembly assigned; distinguish the final runs from earlier simulator tables. |
| Tanay / GitHub handle to confirm | Fix-check verification, gold/negative-control validation and slides assigned. | No corresponding implementation commit/PR was present in the inspected main snapshot. Add his verified links after integration. | Confirm actual verification experiments, report writing and slide contributions directly with Tanay. |

The table describes work rather than using commit counts as a proxy for effort.
[The generated history inventory](../../results/deep/contributions.md) supports
the commit evidence but cannot verify meetings or other offline work. Stakeholder
Sai Rithwik Kukunuri is a different person from Karthik; their names must not be
merged. All members should review this draft before submission. Deep's AI-use
disclosure is appended in [AI_ASSISTANCE.md](../AI_ASSISTANCE.md); retain the full
interaction trace for the course submission.
