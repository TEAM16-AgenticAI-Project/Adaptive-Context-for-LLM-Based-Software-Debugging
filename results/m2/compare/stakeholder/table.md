Backend: groq. Cases: 2.

| System | Role | File acc | Func acc | Grounded | Fix passes tests | Tokens | LLM calls | Wall (s) | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| static_k3 | baseline | 1.000 | 1.000 | 1.000 | 1.000 | 550 | 1.00 | 0.48 | 0 |
| static_k10 | baseline | 1.000 | 1.000 | 1.000 | 1.000 | 701 | 1.00 | 0.42 | 0 |
| adaptive | proposed | 1.000 | 1.000 | 1.000 | 1.000 | 614 | 1.00 | 0.45 | 0 |
| agent_verify | proposed | 1.000 | 1.000 | 1.000 | 1.000 | 800 | 1.00 | 2.53 | 0 |
