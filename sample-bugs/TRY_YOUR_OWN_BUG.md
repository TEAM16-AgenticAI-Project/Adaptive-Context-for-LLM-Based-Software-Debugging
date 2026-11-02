# Try your own bug — worked example

This folder has a ready-made buggy Python repo so you can test the "upload a repo" flow in the
deployed UI without finding one yourself.

## Files
- `cart_app.zip` — the buggy repo, ready to upload (4 Python files).
- `cart_app/` — the same files, unzipped, if you want to read them.

## The bug (so you know what the right answer is)
A shopping cart totals items across three files: `cart.py` -> `pricing.py` -> `tax.py`.
`tax.py::apply_tax` taxes the **full** subtotal instead of the **discounted** amount, so the
total is wrong. The failing case:

```
cart_total([(100.0, 1)], discount_rate=0.20, tax_rate=0.10)  ->  returns 110.0, should be 88.0
```

Correct result: `100 * (1 - 0.20) * (1 + 0.10) = 88.0`.
Root cause: `tax.py::apply_tax` ignores `discount_rate`. Fix: multiply by `(1 - discount_rate)`
before adding tax.

## How to run it in the UI
1. Open your deployed site.
2. **1. Describe the bug** — paste:
   ```
   cart_total([(100.0, 1)], discount_rate=0.20, tax_rate=0.10) returns 110.0 but should
   return 88.0. The tax looks like it is applied to the full price instead of the discounted
   price. Find the root-cause function and suggest a fix.
   ```
   (The same text is in `cart_app/BUG_REPORT.txt`.)
3. **2. Point at the repository** — click the **Upload .zip** tab and choose `cart_app.zip`.
4. **3. Settings** — leave defaults (adaptive, budget 3000). Pick **llm = groq** for a real
   diagnosis (needs the Groq key set on the server); **sim** will show the evidence retrieval
   but not a correct fix for this bug type.
5. Click **Diagnose the bug**.

## What a correct (groq) run should produce
- Diagnosis: file `tax.py`, function `apply_tax`.
- Cause: tax is applied to the full subtotal, not the discounted amount.
- Evidence: `cart.py::cart_total`, `pricing.py::line_subtotal`, `tax.py::apply_tax`, and the
  failing test — found by following the call chain, which is the whole point of adaptive
  context retrieval.
