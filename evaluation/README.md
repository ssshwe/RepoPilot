# Repository evaluation

## Stage 4 Task 4 tokenizer receipts

[`stage4/verify_tokenizer.py`](stage4/verify_tokenizer.py) checks the shared
`code-lexical-v1` rules on the saved Task 3 index and the unchanged regression/dev
questions. It saves complete ordered tokens, term frequencies, source/index and
implementation fingerprints, Unicode identity, and 21 explicit examples.

```powershell
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_tokenizer.py
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_tokenizer.py `
  --verify-only evaluation/reports/stage4_tokenizer_20261015_task4_final.json
```

The final run covers 129 chunks / 52 queries: 3870 source tokens and 975 query
tokens. Reserved queries were not processed, and no model or Qdrant calls occur.
This validates lexical behavior and reproducibility, not retrieval improvement.
Version drift, changed associations, dropped queries and token/frequency changes
fail replay; existing outputs cannot be overwritten. Thirteen new receipt tests
bring the evaluation suite to 130 passing cases. See
[Task 4 rules, examples and reproduction](../docs/stage-4-task-4-tokenizer.md).

## Stage 4 Task 3 candidate compatibility

[`stage4/verify_candidates.py`](stage4/verify_candidates.py) reuses Task 2's
saved Payload/Vector snapshot in a disposable collection. It compares 30 internal
Dense candidates, adapted to Top1/5/20, with the unchanged production service.
All 156 comparisons across 52 regression/development queries match exactly;
37 queries returned more than 20 candidates. Top5 metrics remain the same as
Task 2, and reserved queries remain unqueried.

```powershell
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_candidates.py
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/verify_candidates.py `
  --verify-only evaluation/reports/stage4_candidates_20261013_task3.json
```

One real embedding per case is shared by both paths inside the proof only.
Full candidates, vectors, legacy responses, actual Builder Context, metrics,
configuration and the cloned index are saved. Replay recomputes Cosine scores
and validates identity/ranking, results and evidence. The 52 real embeddings
and 208 Qdrant queries perform no generation or new indexing embeddings.
Original collections and prior reports are preserved; the temporary collection
is deleted. Existing output/receipt directories are never overwritten.
Ten proof-integrity tests bring the evaluation suite to 117 cases. See
[Task 3 contracts and reproduction](../docs/stage-4-task-3-candidates.md).

## Stage 4 Task 2 dataset and metrics

[`datasets/stage4_retrieval.json`](datasets/stage4_retrieval.json) freezes 72
questions: unchanged historical 12, development 40 (32 positive / 8 negative),
and reserved 20. Feature families and negative paraphrases stay within one
split. Whole source manifests, exact AST Symbols, evidence snippets/ranges,
required facts and annotator identities are validated before evaluation.
The new commerce fixture has 29 files / 106 chunks; a shadow repository adds
same-name and scope distractors. These are controlled handwritten learning data.

```powershell
# Real embeddings and Qdrant; default is regression + dev; unique temporary index
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/evaluate.py
# Offline receipt, Context/Citation and metric replay
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/evaluate.py `
  --verify-only evaluation/reports/stage4_retrieval_20261013_task2.json
```

The run preserves ranked chunks and actual ContextBuilder output, complete
index and dataset snapshots, per-case receipts, category/repository/split
summaries and separate indexing/query call costs. No generation occurs.
Complete-range recall and unique-line coverage are different; evidence MRR
uses the first overlap with required lines. Negative controls have N/A positive
metrics and separate empty-result/scope counts. p50/p95 use explicit linear
interpolation on measured service latency.

The real 52-query run preserved historical Hit@5 91.67%. On 32 development
positives: file Hit@5 100%, range recall 79.69%, line coverage 81.27%, complete
evidence/Context 22/32. Six of eight negatives still returned unrelated Dense
results; there were zero foreign-repository results. Temporary index cleanup
and original-index preservation passed. Holdout questions were not queried.
`--split holdout` requires a matching previously frozen `--frozen-config` file.
All 107 evaluation tests passed. See [Task 2 definitions and reproduction](../docs/stage-4-task-2-evaluation.md).

## Stage 3 final acceptance and Stage 4 Dense baseline

The new 2026-10-13 Task 13 run preserves 12 real HTTP retrieval requests and
16 real ASGI grounding requests with 15 native generation calls. The old
datasets/reports remain intact. Canonical retrieval has file Hit@5 91.67%,
complete evidence-range Macro Recall@5 73.61%, Symbol Macro Recall@5 69.05%
(seven labelled queries), and actual Context full coverage 8/12. The new explicit
assistant source review found 5/12 supported and 3/12 complete answers; all
46 Citation coordinates/previews were valid, with the empty control excluded.

Reports are `reports/stage3_retrieval_20261013_task13.json` and
`reports/stage3_grounding_20261013_task13_{raw,review,summary}.json`. Grounding
receipts preserve actual same-request traces. Retrieval `_cases` receipts are
projected rows exported from the real HTTP report, not native full HTTP bodies.
See [final acceptance](../docs/stage-3.md) for tests and browser evidence.

`stage4/freeze_baseline.py` accepts `--retrieval-report`, `--grounding-report`,
`--review-report`, `--acceptance` and `--output`. The acceptance input must bind
the exact new reports and passed Task 13. It checks source, settings, runtime,
index and report identities before and after independent Top20 diagnostic
searches. It never indexes, changes normal routes, or calls generation. Keys
and endpoint URLs are excluded from its configuration snapshot.

The saved baseline is `reports/stage4_dense_20261013_task1.json`; its adjacent
`_artifacts` directory contains full index/configuration snapshots, Chunk
inventory, separate Top20 receipts and Bad Case classifications. The primary
HTTP Top5 and same-request grounding Top5/Context are separately retained.
Top20 never changes their metrics. No native index revision or Git commit is
invented; source/index SHA-256 provide frozen identities.

```powershell
# Fully offline: no Qdrant, embedding or generation calls
& '.\services\api\.venv\Scripts\python.exe' evaluation/stage4/freeze_baseline.py `
  --verify-only --output evaluation/reports/stage4_dense_20261013_task1.json
```

See [Task 1 commands, definitions and results](../docs/stage-4-task-1-baseline.md).
Changed sources/reports/artifacts abort replay. New runs require new output
paths; existing baselines and even partial diagnostic directories are never
overwritten. Per-line coverage and the new dataset are now provided separately
by Task 2 above; Task 1's frozen metric definitions/results remain unchanged.

## Stage 3 grounding evaluation (Task 12)

[`datasets/stage3_grounding.json`](datasets/stage3_grounding.json) freezes 16
source-grounded cases before the run: the unchanged 12 Stage 2 queries, two
Issue questions, an empty repository control and a 700-character Context case.
It uses the existing 17-point AST/fallback index and unchanged local providers.

From the project root:

```powershell
.\infra\Qdrant.ps1 up
& '.\services\api\.venv\Scripts\python.exe' '.\evaluation\stage3\evaluate.py'
& '.\services\api\.venv\Scripts\python.exe' -m pytest '.\evaluation\tests' -q
```

The evaluator calls existing FastAPI routes with HTTPX ASGITransport inside its
own process. Embedding/Qdrant/generation calls are real network calls; no model
response is mocked and the normal API process is untouched. Observation hooks
record the same request's retrieval, Context, model JSON and mapped Sources,
then restore the original methods and close the owned SDK. Only the supplementary
budget case overrides its builder inside this isolated process. The script
reads the existing index, verifies source hashes and before/after Qdrant state,
and never indexes or creates collections.

Default output uses a new timestamped filename under `reports`; `--output` and
`--dataset` are available. Existing reports or per-case receipt directories
cannot be overwritten. Each completed case is saved separately, including HTTP
errors. Structural/source inconsistencies abort the run; already-saved receipts
remain. Generation errors and model abstentions on labelled questions must stay
in the answer scoring denominator.

The raw report is not an answer-quality verdict. Inspect the actual response,
selected Sources, Context and fixed rubric, then create an explicit review JSON.
The review schema example is
[`reports/stage3_grounding_20261012_task12_review.json`](reports/stage3_grounding_20261012_task12_review.json).
Bind it to the current raw report SHA and each response fingerprint; do not reuse
old verdicts to judge newly generated answers. Set the reviewer role and method
truthfully. The saved review was done by the development assistant with direct
source comparison, with independent human review pending.

```powershell
& '.\services\api\.venv\Scripts\python.exe' '.\evaluation\stage3\review.py' `
  --report evaluation/reports/stage3_grounding_20261012_task12_raw.json `
  --review evaluation/reports/stage3_grounding_20261012_task12_review.json `
  --output evaluation/reports/my_stage3_review_summary.json
```

Retrieval file metrics keep the original chunk cutoffs at 1/3/5. Annotated-line
metrics require full coverage of each labelled range, including union coverage
across adjacent chunks. Symbol metrics match exact Path + Symbol and apply only
to seven named-Symbol canonical queries. Context coverage uses emitted slices;
it can fall below retrieval coverage after truncation. Four supplementary cases
never alter the canonical 12-query denominator.

Answer Supported means a substantive, Query-relevant response can be supported
by its selected citation slices. A supported partial answer can still be
incomplete. Repeated questions, wrong locations and wrong scopes fail. Coordinate
validity and whether all selected Citations actually support the answer are
separate measures. Empty negative controls have no Citation accuracy rather than
being counted as perfect citations. Failed labelled answers remain failures.

The [real run](reports/stage3_grounding_20261012_task12_raw.json) used 16 route
requests and 15 native model HTTP calls. Canonical file Hit@5 was 91.67%; line
Macro Recall@5 was 73.61%; Symbol Macro Recall@5 was 69.05%. The source review
found 5/12 supported and 3/12 complete answers. Across all cases, 46 Citation
coordinates/previews in 15 cited responses were valid; the empty control was N/A.
The current small model still gives false or unhelpful answers. This is a small,
previously observed learning fixture, not a held-out production benchmark.

See [`../docs/stage-3-task-12-evaluation.md`](../docs/stage-3-task-12-evaluation.md)
and the [review summary](reports/stage3_grounding_20261012_task12_summary.json)
for all case judgments and real bad cases. Offline evaluator tests use explicit
fixtures and are not model-quality results. There are now 61 evaluation tests
(42 existing + 19 new) at Task 12. Task 13 final functional acceptance is now
complete; 20 Dense baseline tests bring the current evaluation suite to 81.

## Stage 2 retrieval evaluation and historical runs

Stage 3 adds [`retrieval/compare_chunking.py`](retrieval/compare_chunking.py).
Run it with the existing backend environment, configured embeddings and Qdrant:

```powershell
& '.\services\api\.venv\Scripts\python.exe' '.\evaluation\retrieval\compare_chunking.py'
```

It creates two uniquely named temporary collections, indexes the same scanned
repository with the current Simple and AST chunkers, and runs the unchanged 12
queries through `RetrievalService`. Each query uses one real provider embedding
shared by both dense searches. The repository filter, Cosine distance, TopK=5,
model, budgets and existing file metrics stay the same. Additional evidence
metrics check whether the union of TopK ranges covers every annotated line;
they supplement file relevance and do not measure answer correctness.
Reports include ranked Score/Path/Symbol/Node Type/Lines/Strategy/Version,
query-vector fingerprints and preservation checks, with no full code or vectors.
It checks current source content, leaves existing collections unchanged, and
deletes only its temporary collections on success or failure. An unconfirmed
cleanup fails the run and prints the temporary names for recovery. It compares
retrieval quality, not latency: both searches reuse a prepared query vector.
Use `--output` for a new filename; existing reports are never overwritten.

The real Task 5 comparison is
[`reports/stage3_chunking_20261007_task5.json`](reports/stage3_chunking_20261007_task5.json).
AST improved file Hit@1 from 50% to 66.67%, while Hit@5 fell from 100% to 91.67%
and Macro Recall@5 fell from 93.06% to 84.72%. Annotated-line Recall@5 was 73.61%
for AST. Detailed failures and implementation notes are in
[`../docs/stage-3-task-5-retrieval-debug.md`](../docs/stage-3-task-5-retrieval-debug.md).

The dataset contains 12 manually annotated Chinese queries against the existing
handwritten `demo-repository` fixture. Every expected path has a line range,
literal source snippet and file SHA-256. The labels were fixed before the first
evaluation run. Ten queries label one file; two label two and three files.
All eight indexed fixture files are covered. Implementation questions exclude
README descriptions; the documentation question explicitly expects README.
This is a small learning dataset, not a held-out production benchmark.

From the repository root, start Qdrant and the normal API. An existing Task 6
index is required. The evaluator never indexes or changes retrieval behavior.

```powershell
.\infra\Qdrant.ps1 up
.\Start-RepoPilot.ps1 -NoBrowser
& '.\services\api\.venv\Scripts\python.exe' '.\evaluation\retrieval\evaluate.py'
```

The script uses only Python's standard library. `--api-base-url`, `--dataset`,
`--workspaces`, `--timeout` and `--output` are configurable. Without `--output`,
it creates a timestamped JSON file under `evaluation/reports`. Existing files
are never overwritten. API credentials in URLs are rejected and upstream error
bodies are not printed. Reports store queries, annotations, ranked source
metadata, raw scores, model/dimension, dataset/source hashes and per-query
metrics; they omit full code and vectors.

## Metric definitions

For query q, let E(q) be its unique expected file paths. Let R_K(q) be the unique
paths appearing in the first K returned **code chunks**. The cutoff is applied
before deduplication: two chunks from one file occupy two rank positions.

- Per-query Hit@K = 1 if `E(q) ∩ R_K(q)` is nonempty, else 0.
- Report Hit@K = number of queries with a hit / total queries.
- Per-query Recall@K = `|E(q) ∩ R_K(q)| / |E(q)|`.
- Report Macro Recall@K = arithmetic mean of per-query Recall@K, giving every
  query equal weight. It is not pooled/micro recall over all labelled files.

Example: expected A, B, C and only A in Top5 means Hit@5=true but Recall@5=1/3.
Single-file queries have equal Hit and Recall; multi-file queries reveal the
difference. Paths match exactly, including case and directories. Multiple
chunks from one expected file count once in the recall numerator.

Each query makes one real `POST /api/retrieval/search` with `top_k: 5`; metrics
at 1, 3 and 5 use prefixes of that same response. Rankings are preserved, with
no file deduplication before K, score threshold, query expansion or reranking.
Empty successful responses remain misses in the denominator. HTTP errors,
malformed/foreign results, changed model/collection, altered labels/source or
stale returned content abort the run with exit code 1; no completed report is
saved and failures are never dropped to improve the denominator. Source
content is checked against local files before computing metrics. Label-source
hashes are checked before and after evaluation.

These labels describe relevance at the **file** level. They do not evaluate
line-level relevance, answer correctness, or whether an LLM could generate a
grounded answer. The prototype still stops at retrieval.

## Offline tests

From the repository root, using the existing backend development environment:

```powershell
& '.\services\api\.venv\Scripts\python.exe' -m pytest '.\evaluation\tests' -q
```

The tests cover metric definitions, duplicate chunks, exact paths, empty misses,
annotation evidence, stale source, invalid responses, model changes, failed
runs and report preservation. Their fake search responses are unit-test inputs,
not evaluation results. Real reports are produced only by the CLI against the
running normal API.

## First real run

[`reports/stage2_retrieval_20261004_01.json`](reports/stage2_retrieval_20261004_01.json)
contains the actual 12-query run against `repopilot_code`, using
`qwen3-embedding:0.6b`, dimension 1024. All 12 responses returned five chunks.

| K | Hit@K | Macro Recall@K |
| ---: | ---: | ---: |
| 1 | 6/12 (50.00%) | 50.00% |
| 3 | 10/12 (83.33%) | 77.78% |
| 5 | 12/12 (100.00%) | 93.06% |

The two multi-file queries missed the wallet implementation in Top5, giving
recalls of 1/2 and 2/3. All ten single-file cases had Recall@5=1, so the macro
value is `(10 + 1/2 + 2/3) / 12`. The report preserves full-precision numbers.
This eight-chunk fixture makes Top5 relatively broad; 100% Hit@5 is not proof
of complete retrieval or production performance. No labels or queries were
adjusted after observing this run.

The 32 offline tests passed. Dataset/source/index fingerprints stayed unchanged;
all eight persistent points remain. The saved metrics were independently
recomputed from the actual ranked paths in
[`../docs/stage-2-task-9-verification.json`](../docs/stage-2-task-9-verification.json).

## Final Stage 2 acceptance run

[`reports/stage2_retrieval_20261004_task10.json`](reports/stage2_retrieval_20261004_task10.json)
preserves the separate Task 10 rerun. All 12 queries returned five chunks and
the aggregate Hit/Macro Recall values above were unchanged. The dataset,
fixture, model configuration and all eight stored vectors/payloads were
preserved; no labels or query wording were tuned. The two multi-file questions
still missed WalletService in Top5. Original per-request scores and latencies
remain in each report independently.

Final backend/frontend checks, integration-test correction and source/index
preservation are recorded in
[`../docs/stage-2-task-10-verification.json`](../docs/stage-2-task-10-verification.json).
The first report remains intact; use the default timestamped output to make a
new run instead of passing either saved report path again.
