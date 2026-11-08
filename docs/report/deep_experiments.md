# Deep's measured experiments — November 1, 2026

These are dataset-validation and retrieval measurements. No Groq/model diagnosis
run was executed. Each machine-readable artifact identifies its source commit.

## Dataset validation

| Dataset | Attempted | Reproduced | Evidence |
| --- | --- | --- | --- |
| BugsInPy/thefuck | 32 | 32 | [Builder manifest summary](../../results/deep/datasets.json) |
| Controlled lexical-mismatch | 8 | 8 | [Before/after test results](../../results/deep/controlled/validation.jsonl) |
| BugsInPy/PySnooper | 3 | 2 | [All attempts](../../data/pysnooper/pysnooper.jsonl), [verified subset](../../data/pysnooper/pysnooper.reproduced.jsonl) |

PySnooper-1 passes on both buggy and fixed code on this host. It is retained as
an unsuccessful reproduction and excluded from the verified subset. Initial
missing `python_toolbox`/`future` dependencies were resolved before the final
PySnooper attempt. No platform workaround was used to manufacture a failure.

## Evidence audit of the original 32 reports

BM25 includes a gold function at Top-3 for **11/32 (34.375%)** and Top-10 for
**19/32 (59.375%)**. All gold functions are present at Top-3 for 8/32 and Top-10
for 16/32. Ten stored failing-test outputs explicitly name a gold relative file
path; 28 test modules directly import a gold module. Across the 40 distinct
case/short-name labels, the number of definitions sharing a gold short name has
median 69 and maximum 154. These are static, syntactic observations, not model
reasoning or fresh test-output measurements.
[Full audit](../../results/deep/audit/summary.json).

The controlled stress set has 0/8 BM25 gold hits at both Top-3 and Top-10. Its
bug reports deliberately avoid the hidden defective module and its symbol;
this is a construction check, not generalization evidence.
[Controlled audit](../../results/deep/controlled_audit/summary.json).

## Actual BM25 versus Chroma

The original `ChromaRetriever` fails to add five snapshots because repeated test
function definitions have duplicate AST chunk IDs. The untouched implementation
therefore completes 27/32 cases. Both the error records and the 27 paired
completions are preserved; they are not silently reclassified as successful
runs. A second, explicit comparison variant keeps the **last AST definition per
ID for both methods** before indexing. This variant completes all 32 cases and
does not edit Yadnesh's retriever/chunker implementation.

| Configuration | Method | Paired cases | Gold hit @3 | Gold hit @10 | Index failures across attempted cases |
| --- | --- | --- | --- | --- | --- |
| Original input, completed subset | BM25 | 27 | 9/27 | 16/27 | 0 BM25 failures; subset restricted by Chroma |
| Original input, completed subset | Chroma | 27 | 2/27 | 4/27 | 5/32 |
| Last-definition input variant | BM25 | 32 | 11/32 | 19/32 | 0/32 |
| Last-definition input variant | Chroma | 32 | 3/32 | 5/32 | 0/32 |

Sources: [original comparison](../../results/deep/retrievers/summary.json),
[original per-case errors](../../results/deep/retrievers/audit.jsonl),
[last-definition comparison](../../results/deep/retrievers_deduplicated/summary.json).
All ratios are case-level “at least one gold function” recall with the same
reports/chunk corpus inside each comparison. Chroma ranks only Top-10; its null
gold ranks mean “outside Top-10,” not “absent from the index.” BM25's audit ranks
cover all positive-score hits.

Chroma uses `all-MiniLM-L6-v2` through the existing SentenceTransformer embedding
function on CPU. The cached model revision was
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. Library versions are recorded in
[datasets.json](../../results/deep/datasets.json). BM25 indexes path/name/text,
with the name weighted twice; the current Chroma adapter embeds body text.
Thus the result does not isolate ranking method from representation. The bug
reports contain traceback identifiers that can favor lexical matching. Scores
are relevance scores, not calibrated confidence; no significance or general
embedding-model superiority claim is made. Timings are single-run index/query
observations and are not a production latency benchmark.

## Stakeholder reference

All 32 planned rows are explicitly `not_run`; measured count is zero and metrics
are null. No token, cost, accuracy or agent-performance result is inferred.
[Status artifact](../../results/deep/stakeholder/summary.json).
The adapter and parser/capture tests are ready for a teammate with a Groq key to
run the live reference. The final whole-system results table remains Karthik's
integration/evaluation deliverable.
