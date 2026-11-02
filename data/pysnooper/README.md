# BugsInPy/PySnooper extension

`pysnooper.jsonl` retains all three attempted bugs; `pysnooper.reproduced.jsonl`
contains only the two verified cases (2 and 3). Bug 1's regression test already
passes on this Mac, so it is not claimed as reproduced. The experiment does not
modify a test to force failure. Full source and environment pins are in
`results/deep/datasets.json`.

The existing `bench.bugsinpy` builder exports each fixed commit with its regression
test, reverse-applies the maintainer patch, tests buggy/fixed states in temporary
copies, and restores the buggy source. Gold fields remain evaluator-only.
Third-party exports under `repos/` are ignored; rebuild them before evaluating.

Use the project's Python 3.8 / pytest 6 environment, with
`python_toolbox==1.0.11` and `future==0.18.3` added, and a full PySnooper clone:

```bash
DATA_WORK="$(pwd)/../m2-work"
python -m adaptive_context.bench.bugsinpy --bugsinpy "$DATA_WORK/BugsInPy" --project pysnooper --clone "$DATA_WORK/PySnooper" --out data/pysnooper --bugs 1-3 --env "py38-pytest6=$DATA_WORK/venv38/bin/python" --default-env py38-pytest6
python -m adaptive_context.bench.dataset_summary --manifest data/pysnooper/pysnooper.jsonl --write-filtered --out results/deep/pysnooper_reproduction.json
```

Set `DATA_WORK` to the absolute work directory used by `scripts/setup_env.sh`;
the example uses that script's default layout. Tests run from temporary repos,
so the interpreter path must be absolute. The verified manifest is preparation for a new real-project pilot;
no model localization or fix-acceptance result on these cases is claimed yet.
