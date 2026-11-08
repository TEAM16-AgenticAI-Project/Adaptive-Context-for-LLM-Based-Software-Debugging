# Try the upload flow with cart_app

`cart_app` is a small shopping cart with one bug. It is also available under **Examples**,
but uploading `cart_app.zip` lets you try the same flow you would use for your own code.

## The bug

`cart.py::cart_total` sums the lines with `pricing.py::line_subtotal`, then calls
`tax.py::apply_tax`. `apply_tax` charges tax on the full subtotal and ignores the discount:

```
cart_total([(100.0, 1)], discount_rate=0.20, tax_rate=0.10)  ->  110.0, expected 88.0
```

Expected: `100 * (1 - 0.20) * (1 + 0.10) = 88.0`. The fix is in `tax.py::apply_tax`: multiply
by `(1 - discount_rate)` before adding tax.

## Steps

1. Open the UI and choose **Upload .zip**, then select `cart_app.zip`.
2. Bug report:
   ```
   cart_total([(100.0, 1)], discount_rate=0.20, tax_rate=0.10) returns 110.0 but should
   return 88.0. The tax looks like it is applied to the full price instead of the discounted
   price. Find the root-cause function and suggest a fix.
   ```
3. Choose **Groq** as the model. The simulator will show what each system retrieved but
   will not diagnose this kind of bug.
4. Press **Diagnose**.

A correct run names `tax.py` / `apply_tax`. The evidence list should include `cart_total`,
`line_subtotal`, `apply_tax` and the failing test.
