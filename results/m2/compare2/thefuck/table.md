Backend: groq. Cases: 4.

| System | Role | File acc | Func acc | Grounded | Fix passes tests | Tokens | LLM calls | Wall (s) | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| static_k3 | baseline | 0.750 | 0.500 | 0.500 | 0.000 | 1092 | 1.00 | 5.85 | 0 |
| static_k10 | baseline | 0.750 | 0.750 | 0.750 | 0.000 | 2956 | 1.00 | 16.39 | 0 |
| adaptive | proposed | 0.500 | 0.500 | 0.500 | 0.000 | 699 | 0.50 | 6.82 | 2 |
| agent_verify | proposed | 1.000 | 0.750 | 0.750 | 0.500 | 22282 | 8.25 | 192.35 | 4 |
