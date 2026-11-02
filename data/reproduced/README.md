# Fresh reproduction of dataset B

This new manifest retains the supplied `data/real/thefuck.jsonl` unchanged while
recording a fresh local reconstruction of all 32 BugsInPy/thefuck cases. All 32
fail on their buggy source and pass with the maintainer's patch in the selected
Python 3.8 environment. `thefuck.reproduced.jsonl` is the builder-summary filter;
it contains the same 32 successful cases. Exported project copies are ignored.

For the original report/query comparison, keep the **original** manifest and use
the reconstructed source directories:

```bash
python -m adaptive_context.bench.audit --audit --manifest data/real/thefuck.jsonl --repo-root data/reproduced --out results/deep/audit
```

The supplied and freshly reproduced manifests can have different traceback text
even for the same historical bug. Replacing that text changes the query and can
change retrieval results. The original audit/comparison artifacts record the
original manifest hash; `results/deep/datasets.json` separately records the fresh
reproduction manifest's hash. No maintainer patch is stored inside an indexed case.
