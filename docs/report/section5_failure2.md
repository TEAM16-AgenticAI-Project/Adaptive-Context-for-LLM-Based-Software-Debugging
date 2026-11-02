# Section 5, Failure 2: a passing test does not prove a correct fix (draft, Tanay)

Sources: `results/fixcheck_case_thefuck-5.{json,txt}` (commit `95bd786`) and `results/fixcheck_validation.jsonl`
(commit `ea5365d`). These come from real test runs on dataset B, with no LLM.

## What went wrong

On thefuck-5 we gave `check_fix` a fix that only special-cases the test input:

```python
@git_support
def match(command):
    return ('push' in command.script_parts
            and 'set-upstream' in command.output
            and 'bitbucket' not in command.output)
```

This is not a real fix. Any other Git host whose branch names contain `set-upstream` still triggers the bug. The
fix check still reports `passed` on the regression test, and it also passes the **whole** test module
(`tests/rules/test_git_push.py`). So `agent_verify` would stop with `stop_reason = "verified"`. "Verified" here
means only "passes the tests we have". When the tests are narrow, a model can satisfy them without fixing the
cause.

## Why it happens

The tests are the only judge. BugsInPy gives one regression test per bug, and it was written for the maintainer's
fix, not to rule out every wrong fix. Running the whole test module did not catch the overfitted fix in this case.

## Other limits we found

1. **One function is often not enough.** In 10 of 32 bugs the maintainer's fix cannot be written as one function.
   6 change several functions, 2 change several files, and 2 change module-level code: thefuck-7 removes a
   module constant, and thefuck-28 reorders the module-level `patterns` tuple, because the first matching pattern
   wins. For these the model has to write a unified diff. Diffs are easy to get slightly wrong. We already
   recount hunk sizes (`git apply --recount`), but a diff with wrong context lines is rejected as
   `diff_does_not_apply`. How often the live model gets there is a question for the live runs.
2. **Line mode is a guess.** A one-line fix replaces the most similar line in the function (difflib ratio at least
   0.5). With two similar lines, the wrong one can be replaced. The test then fails, so this costs a retry. It
   does not produce a wrong "verified".
3. **Names must be unique in the file.** If the short name is ambiguous within the file (two classes with
   `run`), the check returns `function_not_found` and asks for the qualified name.

## What we changed and what remains

* Every check reports `mode` and `reason`, so the report can separate "applied and passed", "applied and failed"
  and "rejected before running".
* In the report, a verified fix means "passes the regression test". It is not counted as correct. Correctness of
  the cause comes from the separate root-cause audit (Karthik).
* Remaining: run the whole test module as well as the regression test, and check passing fixes against the
  maintainer's changed functions (did the fix touch the gold function?) to flag suspicious passes.

## Safety note

The fix check runs model-written code. It works on a temporary copy of the repository, with a timeout (120 s by
default) and with API keys removed from the environment, and it never changes the original files (tested). This is
**not a security sandbox**. The code runs as the current user, with the same file system and network access, and
a process that it starts could outlive the timeout. For untrusted code it should run inside a container with no
network. We only run it on our own benchmark repositories.
