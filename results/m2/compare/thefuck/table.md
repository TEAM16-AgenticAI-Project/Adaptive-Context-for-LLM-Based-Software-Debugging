Backend: groq. Cases: 5.

| System | Role | File acc | Func acc | Grounded | Fix passes tests | Tokens | LLM calls | Wall (s) | Failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| static_k3 | baseline | 1.000 | 0.800 | 0.400 | 0.000 | 1330 | 1.00 | 2.78 | 0 |
| static_k10 | baseline | 0.800 | 0.800 | 0.600 | 0.000 | 1884 | 0.80 | 57.68 | 1 |
| adaptive | proposed | 0.600 | 0.600 | 0.600 | 0.000 | 967 | 0.80 | 12.08 | 2 |
| agent_verify | proposed | 0.800 | 0.600 | 0.600 | 0.400 | 28336 | 8.60 | 292.79 | 4 |
