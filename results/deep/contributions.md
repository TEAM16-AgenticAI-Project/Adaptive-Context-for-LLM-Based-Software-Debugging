# Section 7: Team Contributions (from Git history)

Repository: https://github.com/TEAM16-AgenticAI-Project/Adaptive-Context-for-LLM-Based-Software-Debugging

Per-author activity from the commit history (no-merge commits). Commit counts alone are not a complete contribution summary; each member adds their non-commit work (datasets, evaluation, stakeholder communication, writing) below their row.

| Author | Email | Commits | Files touched | Lines + | Lines - |
| --- | --- | --- | --- | --- | --- |
| Yadnesh Deshpande | yadnesh.deshpande1@gmail.com | 9 | 0 | 0 | 0 |
| Deep Nayak | deepnayak200@gmail.com | 4 | 0 | 0 | 0 |
| Karthik Venkata Sai Reddy Kunduru | kkundur1@users.noreply.github.com | 3 | 0 | 0 | 0 |
| Prem Mahendra Pagare | prem.pagare2000@gmail.com | 2 | 0 | 0 | 0 |
| Yadnesh Deshpande | ydeshpa4@asu.edu | 2 | 0 | 0 | 0 |

## Commit subjects per author

### Yadnesh Deshpande
- `516ca64` docs, results: Section 5 failure 1 draft, ablation and name-ambiguity results
- `1a66b12` budget, adaptive: protect the entry function and keep call lines in outlines
- `b2701a2` bench: budget ablation (simulator, dataset A) and name-ambiguity stats (dataset B)
- `603211f` adaptive: whole-prompt budget, pinned requests with recency, stop_reason, wall_s
- `e9a7e41` compress, budget: notes for evicted pinned code (extension A)
- `1a7f1a4` budget: code_budget, pinned chunks, over_budget and dropped_pinned
- `17a04fd` retrieval: resolve callees through the file's imports before name matching
- `1479425` retrieval: lookup accepts dotted module paths, path::Qual, pytest ids, Class.method, short names
- `a31305d` chunking: split module code at statement boundaries instead of truncating at 60 lines

### Deep Nayak
- `1124a2a` fix(bench): support explicit duplicate-definition comparison variant
- `effee48` feat(data): summarize reproduction attempts and filter verified datasets
- `b8e5ec0` feat(bench): adapt stakeholder baseline and test Chroma retrieval
- `d13d4e5` feat(data): add Deep evidence audit and controlled bug validation

### Karthik Venkata Sai Reddy Kunduru
- `2069eba` Clarify eval-harness owner name in CODE_REVIEW (Sai display name -> Karthik); stakeholder references unchanged
- `75daf83` Add results aggregation and Section 7 contributions generator; clarify eval-harness owner name
- `a3eb2bb` Add Milestone 2 results reporting: render bench summary to results/m2 table.md and summary.json

### Prem Mahendra Pagare
- `83e88d8` feat(agent): LangGraph agent with native tool calling
- `3218442` chore: Milestone 2 shared setup, code review and task split

### Yadnesh Deshpande
- `8720e50` first commit
- `6ffa0c0` Add adaptive-context: adaptive retrieval, compression, sequencing, token budget, tools, benchmark
