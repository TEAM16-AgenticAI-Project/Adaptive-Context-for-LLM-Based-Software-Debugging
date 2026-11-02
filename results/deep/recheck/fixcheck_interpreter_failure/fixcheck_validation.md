# Fix-check validation on dataset B (BugsInPy/thefuck, real tests, no LLM)

Commit `018a61bfd1`. Gold fix passes: **0/32**; negative control rejected: **32/32**; unchanged repo fails: **32/32**.

| Case | Gold form | Why | Gold check | Negative control | Unchanged repo |
|---|---|---|---|---|---|
| thefuck-1 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-2 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-3 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-4 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-5 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-6 | diff | multi_function | tests_fail (diff) | rejected: no_change | fails |
| thefuck-7 | diff | module_level | tests_fail (diff) | rejected: no_change | fails |
| thefuck-8 | diff | multi_function | tests_fail (diff) | rejected: no_change | fails |
| thefuck-9 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-10 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-11 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-12 | function | imports+function | tests_fail (function) | rejected: no_change | fails |
| thefuck-13 | diff | multi_function | tests_fail (diff) | rejected: no_change | fails |
| thefuck-14 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-15 | diff | multi_function | tests_fail (diff) | rejected: no_change | fails |
| thefuck-16 | diff | multi_file | tests_fail (diff) | rejected: no_change | fails |
| thefuck-17 | diff | multi_file | tests_fail (diff) | rejected: no_change | fails |
| thefuck-18 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-19 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-20 | diff | multi_function | tests_fail (diff) | rejected: no_change | fails |
| thefuck-21 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-22 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-23 | function | imports+function | tests_fail (function) | rejected: no_change | fails |
| thefuck-24 | diff | multi_function | tests_fail (diff) | rejected: no_change | fails |
| thefuck-25 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-26 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-27 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-28 | diff | module_level | tests_fail (diff) | rejected: no_change | fails |
| thefuck-29 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-30 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-31 | function | single_function | tests_fail (function) | rejected: no_change | fails |
| thefuck-32 | function | single_function | tests_fail (function) | rejected: no_change | fails |
