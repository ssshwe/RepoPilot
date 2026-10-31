# RepoPilot

RepoPilot is a repository-level AI software engineering agent developed in small,
explicit stages. The current codebase completes **Stage 1 / Task 8**, **Stage 2 / Task 10**
and **Stage 3 / Tasks 1–13**, plus **Stage 4 / Tasks 1–10**: a Vue app with LLM, retrieval, Code Chat and Issue Analyzer
workspaces. The LLM workspace exposes normal chat, streaming chat, structured requirement
analysis, and native tool calling through a configurable FastAPI LLM API.
The backend also exposes embedding preview, cosine similarity and read-only
scanning, Simple and Tree-sitter AST-aware chunking, persistent vector indexing,
repository-scoped code search and bounded source context of local test repositories.

The [Stage 3 final acceptance](docs/stage-3.md) records 1006 backend tests
(including five real integration cases), 81 evaluation tests, 46 frontend tests,
the production build, browser controls and a new 16-case grounding run.
The [Stage 4 Dense baseline](docs/stage-4-task-1-baseline.md) binds that new run
to source/configuration/index fingerprints, saves the complete index and checks
separate Top20 diagnostics. It supports offline replay without model calls.
Retrieval Debug now ignores stale responses after cancellation or timeout.
The production retrieval strategy and API contracts are unchanged. The current
model still fully answers only 3/12 historical questions; answer quality remains
separate from functional acceptance.

[Stage 4 Task 2](docs/stage-4-task-2-evaluation.md) adds 60 source-grounded
learning questions (40 development / 20 reserved), alongside the unchanged
12 historical questions. Its evaluator distinguishes complete ranges, unique
required lines, evidence MRR and actual Builder Sources; it saves real results
and supports offline recomputation. The 52-query development/regression run
used a disposable 129-point index with same-name and cross-repository distractors.
Reserved questions were not queried, no generation ran, and production data was
preserved. The evaluation test suite now has 107 passing cases.

[Stage 4 Task 3](docs/stage-4-task-3-candidates.md) adds an internal candidate
type, recall-channel interface, Dense recall up to 60 and a bounded final-result
adapter. A real 52-query proof used 30 candidates and matched the original
Dense Top1/5/20 results in all 156 comparisons. Production still uses its original
Dense path; final API results stay within 20 and Context keeps its own budget.
The proof reused saved vectors in a temporary index and is replayable offline.
The evaluation suite now has 117 passing cases.

[Stage 4 Task 4](docs/stage-4-task-4-tokenizer.md) adds versioned code tokenization
shared by indexed code and queries: complete identifiers, camel/snake/acronym
parts, paths, error text and Chinese character bigrams. Its offline receipts cover
129 frozen chunks and 52 development/regression queries without model calls or
index writes. All 72 tokenizer tests pass; the current default backend suite has
1128 passing tests and the evaluation suite has 130. Production retrieval remains
Dense, with BM25 still scheduled for a later task.

[Stage 4 Task 5](docs/stage-4-task-5-snapshots.md) adds durable shared lexical
snapshots, immutable indexed code/Tokens, scoped writer leases and explicit
ready/rebuilding/incomplete states. A declared complete Chunk inventory and
before/after Dense checks prevent publishing a partial rebuild or combining it
with old lexical data. A 14-phase real Qdrant proof covers restart, source edits,
deletion, failure and recovery using private fixtures; production data stays
unchanged. The production API still uses the original Dense path.
The current default backend suite passes 1160 tests (6 skipped), and the
evaluation suite passes 141 tests.

[Stage 4 Task 6](docs/stage-4-task-6-bm25.md) adds an independent BM25 candidate
channel over shared snapshots, repository-local TF/IDF statistics and per-term
score explanations. Its 52-case comparison reuses the same indexed code and
the saved Dense results without model calls. Development range recall rises
from 79.69% to 86.46%, while complete evidence on historical questions drops
from 8/12 to 3/12. Production remains Dense. The current default backend suite
passes 1177 tests (6 skipped), and evaluation passes 155 tests.

[Stage 4 Task 7](docs/stage-4-task-7-exact.md) adds independent Exact locators
for Symbols, paths, filenames, error text and raw code literals, with full/partial
match reasons and source coordinates. Shared repository/language/path-prefix
filters apply before the candidate limit in all three internal channels.
Twenty-four frozen probes, 52 unchanged BM25 results and five real Qdrant
filter scenarios are saved and replayable offline. The current default backend
suite passes 1221 tests (6 skipped), and evaluation passes 172 tests.
Production still uses Dense; Hybrid/RRF is the next task.

[Stage 4 Task 8](docs/stage-4-task-8-hybrid.md) adds guarded Hybrid retrieval
and 1-based RRF with k=60. It preserves original channel ranks/scores, rejects
scope/version/source conflicts, and returns at most 60 fused candidates before
the existing final TopK adapter. On the same 52 frozen questions, development
complete-range Recall@5 reaches 89.58% (Dense 79.69%); complete evidence rises
from 22/32 to 24/32. Historical complete evidence stays at 8/12 while MRR declines
from 0.6042 to 0.4667. Three real Qdrant Hybrid scenarios and offline replay pass.
The default backend suite passes 1254 tests (6 skipped), and evaluation passes
190 tests. Production remains Dense; Retrieval Debug integration is Task 9.

[Stage 4 Task 9](docs/stage-4-task-9-retrieval-debug.md) connects the Debug page
to Dense/BM25/Hybrid modes and shared language/path filters. It shows complete
channel candidates, source and fusion ranks, final TopK, Exact reasons, RRF
contributions, typed scores, timings and the indexed snapshot SHA-256. Explicit
snapshot synchronization reads the existing index into local lexical storage.
Nine real HTTP cases and browser flows pass, including cancellation with a late
response, source expansion and mobile layout. The current suites pass 1279
backend tests (6 skipped), 207 evaluation tests and 76 frontend tests; the build
passes. Legacy search, Code Chat and Issue Analyzer retain Dense retrieval.
Rerank/Expansion controls remain reserved and disabled in the page/API.

[Stage 4 Task 10](docs/stage-4-task-10-reranker.md) adds an independent optional
BGE reranker with a pinned offline model, bounded single-worker execution,
kill-and-reap timeouts, safe fusion fallback and preserved Chunk identities.
On the same 52 frozen questions, development range Recall@5 rises from 89.58%
to 92.71% (complete evidence 24/32 → 25/32); historical complete evidence rises
from 8/12 to 9/12. Rerank-stage P50/P95 are 183/224ms on the target RTX 4060.
Nine length/batch probes, real process-lock/timeout/missing-model checks and
offline score/Context replay are retained. No new Embedding, generation or
holdout calls were made. Default remains off pending Task 14; Task 11 is next.

Stage 2 **finds code**; Stage 3 Task 7 adds a backend RAG endpoint using real
retrieved context. The Retrieval Debug endpoint still returns source chunks.
Start with the
[final acceptance and concept guide](docs/stage-2.md#task-10-final-tests-documentation-and-acceptance)
and the [25-question Chinese study guide](docs/stage-2-acceptance.md).
Earlier task sections below retain their original experiment boundaries.

## Stage 3 / Task 6: build source context

The independent `ContextBuilder` validates all retrieved metadata and repository
identities, removes overlapping lines per file, preserves retrieval rank, applies
source and serialized character limits, then emits backend-generated `S1`, `S2`,
etc. Sources use explicit boundaries and JSON-encoded metadata/content. Truncation
keeps complete source lines and updates the actual line range; the original range
remains available for diagnostics.

With the existing Qdrant index and configured local embedding provider running,
run from the repository root:

```powershell
.\services\api\.venv\Scripts\python.exe services/api/scripts/context_spike.py `
  --repository demo-repository `
  --query "退款服务在哪里校验订单编号，然后把退款金额转回钱包？" `
  --top-k 5 --max-chunks 3 --max-characters 700 `
  --output output/context.json
```

The output directory must exist. `--output` writes UTF-8 directly, avoiding
PowerShell pipeline transcoding; omit it to print JSON. Defaults are
`MAX_CONTEXT_CHUNKS=5` and `MAX_CONTEXT_CHARACTERS=16000` in `.env.example`.
The character limit includes source boundaries, metadata, escaping and separators;
it counts Unicode characters, not tokens. This CLI reads the existing index and
calls the embedding provider for the query. It stops at context and does not call
the generation provider or re-index the repository.

See the [Task 6 acceptance and concept guide](docs/stage-3-task-6-context-builder.md)
and [Task 5 retrieval comparison](docs/stage-3-task-5-retrieval-debug.md).
`POST /api/code-chat` is available below, with backend-mapped Citations from Task 8.

## Stage 3 / Task 7: ask indexed code

With the configured API, local Ollama and existing Qdrant index running:

```powershell
$body = @'
{"repository":"demo-repository","question":"退款服务在哪里校验订单编号，然后把退款金额转回钱包？","top_k":5}
'@
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/code-chat `
  -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

The fixed pipeline retrieves once, builds bounded context and calls the existing
LLM Provider once through structured generation. Its schema allows only `answer`
and this request's `source_ids`; the API adds repository, model, status and
retrieval/generation diagnostics. Empty context returns `insufficient_evidence`
without resolving the LLM. An empty generated ID list returns the same controlled
evidence-insufficient answer. Malformed output returns a safe HTTP 502 with no
repair loop. Repository data is explicitly untrusted in a dedicated RAG system
prompt and remains in user messages.

The response retains Source IDs and now includes backend-mapped `citations`.
Valid JSON and valid IDs do not prove that an answer is supported. The current
local model can answer too briefly or select unsupported evidence; see the real
before/after bad case in the [Task 7 guide](docs/stage-3-task-7-rag-backend.md).

## Stage 3 / Task 8: validate Citations

The independent, pure `CitationValidator` checks the entire emitted Source map
against this request's retrieved chunks and formatted context. It rejects
unknown/duplicate IDs, foreign repositories, mismatched metadata, invalid ranges
and modified source slices. Invalid input fails the entire response with a safe
HTTP 502; no partial Citation batch or answer is returned.

Only the model-selected IDs are mapped. Path, Symbol and line ranges come from
validated backend metadata, with the actual emitted slice taking precedence over
the original chunk range. A 700-character real context produces a Citation for
`RefundService.refund` at lines 12–15, rather than the original 12–18.
Empty context or model abstention yields `citations: []`.

The model still generates only `answer` and `source_ids`; it never generates the
Citation objects. Metadata validity does not establish semantic answer correctness
or current file freshness. See the [Task 8 guide](docs/stage-3-task-8-citation-validator.md)
for actual full/truncated/empty HTTP results and the remaining answer bad case.

## Stage 3 / Task 9: basic prompt-injection defenses

The RAG system prompt explicitly treats repository metadata, README, comments,
Issue text and code strings as untrusted data. Forged role labels or delimiters
cannot grant instruction priority. It prohibits Secret/environment-value output
and treating repository commands as tool instructions. Context stays in user
messages, uses the existing JSON Source framing, and counts the reinforced
envelope toward the existing message limit. Code Chat advertises no tools.

Known sensitive filenames remain excluded before reading/chunking. A small
backend output check additionally rejects credential-value assignments and
private-key blocks with a safe HTTP 502, returning no partial answer/Citations.
It can reject harmless code examples and miss other secret representations;
it is not a complete Secret detector or prompt-injection solution.

Real smoke tests used three deliberately adversarial sample repositories and a
separate temporary Qdrant collection, which was removed after each run. The
production index, environment and previous evidence remain unchanged. A real
Secret request initially produced fabricated values; the final output is blocked.
The small model also gives incorrect explanations or malformed output, so these
tests establish limited controls, not universal model resistance or answer quality.
See the [Task 9 audit and acceptance guide](docs/stage-3-task-9-prompt-injection.md)
for paired real outputs, test scope and remaining risks.

## Stage 3 / Task 10: Code Chat frontend

Start the existing API and web app with `.\Start-RepoPilot.ps1 -NoBrowser`, then
open `http://localhost:5173/#code-chat`. Select or enter an indexed Repository,
set TopK (1–20), and ask a Question. The page displays the actual Answer and
expandable Sources with Path, nullable Symbol, actual Lines and a numbered Code
Preview. The first Source opens by default; long code scrolls within its preview.
The LLM and Retrieval Debug workspaces remain available.

`POST /api/code-chat` now echoes the exact `question` and adds `sources` with
`source_id`, `language` and `content`. These previews come directly from the
validated, cited Context slices. They require no second retrieval or file read;
the six-field Citation contract and model-generated `answer` / `source_ids`
contract remain the same. Clients using the old response contract must accept
the new API fields. Empty evidence returns empty Source and Citation arrays.

The frontend checks the response's repository/question, Source IDs, Citation
ordering and preview line counts before displaying it. It renders answers and
code as escaped text, without HTML or Markdown execution. Pending requests can
be stopped or cleared; late replies cannot restore cleared history. The 20-turn
history is browser memory only, and each question independently retrieves code.
Stopping the browser wait does not guarantee cancellation of server inference.

See the [Task 10 acceptance and concept guide](docs/stage-3-task-10-code-chat-frontend.md)
for real HTTP examples, desktop/mobile screenshots and controlled browser checks.
Persistent/multiturn conversations and streaming Code Chat remain
future tasks. The current small model still returns overly brief answers; valid
citations do not establish answer quality.

## Stage 3 / Task 11: Issue Analyzer

Start Qdrant with `.\infra\Qdrant.ps1 up`, then start the API and web app with
`.\Start-RepoPilot.ps1 -NoBrowser`. Open `http://localhost:5173/#issue-analysis`
and submit an Issue against an indexed Repository. The page displays Task Summary,
Related Code, Possible Call Chain, Analysis, Modification Suggestions, Test
Suggestions, Risks and expandable Sources. It generates suggestions for human
review and does not modify repository files.

```powershell
$body = @'
{"repository":"demo-repository","issue":"商城退款接口可能存在重复退款问题，请分析应该重点检查哪些代码。","top_k":5}
'@
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/issue-analysis `
  -ContentType "application/json" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

The independent Issue service retrieves once, builds bounded context and calls
the existing structured LLM Provider once. The model generates seven fields:
summary, candidate-chain Source IDs, four text arrays and cited Source IDs.
The current generation schema requires one short item per text array. Related
Code, chain coordinates and Code Previews are mapped from the same validated
Context. The model never generates structured Path / Symbol / Lines. Empty
context skips generation; model abstention discards all generated analysis and
suggestions. Invalid output fails the entire response without a repair loop.

The frontend validates request identity, Source ordering, chain metadata and
preview line counts, then renders escaped text. Stop, clear and retry use the
submitted request snapshot and ignore late replies. `analyzed` means the pipeline
returned cited output; the page labels it “附带代码引用”. It does not certify
analysis quality or actual call relationships.

Real calls with the unchanged local `qwen2.5:0.5b` exposed poor analysis: repeated
Issue text, Context markers as suggestions, and a README selected as a candidate
call node. A Secret request returned no actual credentials or tool calls, but the
model asked the user to provide credential values. The finite output check did
not block that wording. These are recorded failures, not successful model-safety
or answer-quality results. See the [Task 11 acceptance and concept guide](docs/stage-3-task-11-issue-analyzer.md)
and [machine verification record](docs/stage-3-task-11-verification.json).
Task 12 evaluation is recorded below.

## Stage 3 / Task 12: grounding evaluation and bad cases

The [fixed Stage 3 dataset](evaluation/datasets/stage3_grounding.json) retains
the original 12 Stage 2 queries and adds two Issue cases, an empty repository
control and an explicit 700-character Context case. Labels and source fingerprints
were frozen before this run. The evaluator invokes existing FastAPI routes through
an in-process ASGI transport, while Embedding and LLM calls use the unchanged real
providers over HTTP. It observes the same request's retrieval, Context, native
model replies and final Sources. It does not modify the normal API process,
production settings, repository files or indexes.

```powershell
.\infra\Qdrant.ps1 up
& '.\services\api\.venv\Scripts\python.exe' '.\evaluation\stage3\evaluate.py'
& '.\services\api\.venv\Scripts\python.exe' -m pytest '.\evaluation\tests' -q
```

The runner writes a new timestamped raw report and per-case receipts, then waits
for explicit source review. It never treats HTTP 200 or valid Citation coordinates
as a correct answer. Review files identify their reviewer and bind verdicts to
the exact report/response fingerprints. The saved Task 12 review was performed
case by case by the development assistant; independent human review is pending.

The [actual 16-case run](evaluation/reports/stage3_grounding_20261012_task12_raw.json)
made 15 native generation calls; the empty case skipped generation. For the 12
canonical queries, file Hit@5 was 11/12 (91.67%), annotated-line Macro Recall@5
was 73.61%, and expected-Symbol Macro Recall@5 was 69.05% across seven labelled
queries. Five answers were supported by selected evidence; three fully answered
the query. All 46 Citations across 15 cited responses had valid coordinates and
previews, which does not establish semantic support.

Recorded failures include a controller file hit without its entry method,
an incorrect answer despite a correct SQL Source, denial of a visible zero-balance
default, a README used as a call node, and an unhelpful answer after real Context
truncation. See the [Task 12 guide](docs/stage-3-task-12-evaluation.md) for review
criteria, all cases, reproduction commands and limits. The 61 offline evaluation
tests passed. Application source, old evaluation artifacts and Qdrant data were
preserved. At that Task 12 milestone, final tests/documentation were pending;
the new Task 13 run and final acceptance are now recorded in
[the Stage 3 guide](docs/stage-3.md).

## Prerequisites

- Node.js 20.19 or newer (verified here with 24.16.0)
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Ollama with `qwen2.5:0.5b`, or another OpenAI-compatible model service

If uv was installed with pip but its scripts directory is not on `PATH`, replace
`uv` in the commands below with `python -m uv`.

## Stage 2 / Task 2: start Qdrant independently

Qdrant stores vectors and their payloads. This task adds its infrastructure and
collection management only. Repository indexing and search are later
tasks. See [Stage 2 notes](docs/stage-2.md) for the data model and API boundaries.

The root `.env.example` now includes these settings:

```dotenv
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
QDRANT_COLLECTION=repopilot_code
QDRANT_TIMEOUT_SECONDS=5
QDRANT_HTTP_PORT=6333
```

`QDRANT_URL` is the backend connection address. `QDRANT_HTTP_PORT` controls the
Compose host port; update the URL too if you change it. An empty key is supported
for local development. Compose forwards only the selected Qdrant settings to the
container, not the LLM credentials. Its port is bound to loopback and its named
volume `repopilot_qdrant_storage` preserves data when the container is stopped.

From the repository root, with Docker Engine and Compose installed:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --wait qdrant
docker compose --env-file .env -f infra/docker-compose.yml ps
```

On this Windows workspace, Docker Engine and Compose run in the existing WSL2
`Ubuntu` distribution. This helper chooses Windows Docker when available, or
WSL Docker otherwise, and handles Windows paths. On WSL it also maintains a
hidden keep-alive process so the database stays reachable after the command exits;
`stop` stops the container and releases that process:

```powershell
.\infra\Qdrant.ps1 up
.\infra\Qdrant.ps1 status
.\infra\Qdrant.ps1 stop
```

For a new Ubuntu WSL installation, the distribution packages used here can be
installed inside Ubuntu with `sudo apt-get update` followed by
`sudo apt-get install --no-install-recommends docker.io docker-compose-v2`.
The helper starts the Docker service through systemd. Qdrant uses the pinned
official image `qdrant/qdrant:v1.19.1`.

After `uv sync --locked --dev` and starting the API as below:

```powershell
Invoke-RestMethod http://localhost:6333/readyz
Invoke-RestMethod http://localhost:8000/health/qdrant
```

`/health` remains the Stage 1 process check. `/health/qdrant` performs an
authenticated collection-list request: it returns HTTP 503 on connection or
credential failure. A healthy server can report `collection_exists: false`.
No collection is created automatically because its dimension must match the
future embedding model.

### Experiment with an empty collection

Use a separate smoke-test collection for the three-dimensional infrastructure
exercise. Three is an experiment dimension, not a measured embedding dimension.
In the backend terminal, before starting Uvicorn:

```powershell
cd services/api
$env:QDRANT_COLLECTION = 'repopilot_infra_smoke'
python -m uv run --locked uvicorn app.main:app --reload
```

In another terminal:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/vector-store/collection `
  -ContentType 'application/json' -Body '{"dimension":3}'
Invoke-RestMethod http://localhost:8000/api/vector-store/collection
```

Repeating the POST returns `created: false`. A different dimension or incompatible
distance returns HTTP 409 and preserves the existing collection. The API manages
only `QDRANT_COLLECTION` and does not offer destructive deletion or recreation.
After the experiment, remove the temporary environment override and restart the
API to use `repopilot_code` again:

```powershell
Remove-Item Env:QDRANT_COLLECTION
```

The default pytest suite requires no Qdrant server. An optional test connects
FastAPI to the real server, creates a uniquely named empty test collection, checks
idempotency and dimension conflicts, and removes only that test collection:

```powershell
cd services/api
$env:REPOPILOT_QDRANT_INTEGRATION = '1'
python -m uv run --locked pytest tests/test_qdrant_integration.py
Remove-Item Env:REPOPILOT_QDRANT_INTEGRATION
```

## Stage 2 / Task 3: local embedding experiments

Install the separate embedding model before calling the new endpoints:

```powershell
ollama pull qwen3-embedding:0.6b
```

The root environment template contains independent settings:

```dotenv
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_API_KEY=ollama
EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1
EMBEDDING_MODEL=qwen3-embedding:0.6b
EMBEDDING_TIMEOUT_SECONDS=120
EMBEDDING_BATCH_SIZE=16
```

`ollama` is a non-secret SDK placeholder. These fields do not inherit any
`LLM_*` values. Start or restart the API after editing `.env`. The embedding
endpoints require Ollama and the downloaded model; they do not require Qdrant.
The one-click launcher starts Ollama but does not download this model for you.

With the API on port 8000, run the real experiment in PowerShell. Explicit UTF-8
encoding preserves the Chinese inputs on Windows PowerShell 5.1:

```powershell
$debugBody = @{ text = '订单退款' } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/embeddings/debug `
  -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes($debugBody))

foreach ($comparison in @('退还订单金额', '今天天气不错')) {
  $pairBody = @{ text_a = '订单退款'; text_b = $comparison } | ConvertTo-Json -Compress
  Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/embeddings/similarity `
    -ContentType 'application/json; charset=utf-8' `
    -Body ([Text.Encoding]::UTF8.GetBytes($pairBody))
}
```

Debug returns `model`, actual `dimension` and at most eight preview coordinates.
Similarity returns `model`, `dimension` and a cosine `similarity` in `[-1, 1]`.
Blank, non-string, oversized (over 8192 characters) or extra input fields return
422; missing provider configuration returns 503; upstream failures or invalid
vectors return 502 with safe error messages. No complete vector is returned.

Default backend tests are offline. To explicitly test the configured embedding
service (a remote configuration would send these fixed texts to that service):

```powershell
cd services/api
$env:REPOPILOT_EMBEDDING_INTEGRATION = '1'
try {
  python -m uv run --locked pytest tests/test_embedding_integration.py
} finally {
  Remove-Item Env:REPOPILOT_EMBEDDING_INTEGRATION
}
```

The implementation and measured results are explained in
[Stage 2 notes](docs/stage-2.md). These experiments use model inference, without
training or fine-tuning, and do not scan repositories or write vectors to Qdrant.

## Stage 2 / Task 4: scan a local repository

The supplied `workspaces/demo-repository/` is a small hand-written test repository
containing actual Java, Vue, TypeScript, JSON, SQL and Markdown files. Put other
test repositories in their own direct child directories of `workspaces/`.
The API accepts a name such as `demo-repository`; it cannot accept an arbitrary
filesystem path. It runs independently of Ollama and Qdrant.

Start the API as described below, then call:

```powershell
$scanBody = '{"repository":"demo-repository"}'
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/repositories/scan `
  -ContentType 'application/json' -Body $scanBody | ConvertTo-Json -Depth 8
```

The measured result for the supplied fixture is:

```json
{
  "repository": "demo-repository",
  "files_scanned": 10,
  "supported_files": 8,
  "skipped_files": 2,
  "total_bytes": 4076
}
```

The full response also includes `files` (relative path, language, byte size and
line count), `skipped` (path and reason) and `pruned_directories`. It omits code
contents and absolute server paths. The sample's `notes.txt` is unsupported;
`package-lock.json` is ignored. Counts are computed from the scan, not hardcoded.
Ignored directories are pruned and their descendants are not counted. Links and
special entries are reported as skipped candidates rather than followed.

Supported extensions are `.py`, `.java`, `.js`, `.ts`, `.vue`, `.json`, `.yml`,
`.yaml`, `.md` and `.sql`, compared without case sensitivity. UTF-8 text with
an optional BOM is accepted; binary and invalid UTF-8 files are skipped. The fixed
ignore rules cover the required dependency, build, VCS and editor directories,
plus environment/key files, lock files and minified JavaScript. This version does
not interpret a repository's `.gitignore`.

Scanner resource settings in `.env.example` (defaults apply without adding them
to an existing `.env`):

```dotenv
REPOSITORY_MAX_FILE_BYTES=1048576
REPOSITORY_MAX_ENTRIES=10000
REPOSITORY_MAX_TOTAL_BYTES=20971520
```

Oversized individual files are skipped. Exceeding the total entry or accepted
text byte budget returns 413 instead of an incomplete success response. Invalid
names / extra request fields return 422, a missing repository returns 404, and
an unsafe or inaccessible repository root returns 403. Symlinks and Windows
junctions are not followed. Restart the API after changing settings.

Run the scanner tests without any external services:

```powershell
cd services/api
python -m uv run --locked pytest tests/test_repository_scanner.py -rs
```

There is no scanner page in the Vue application yet. Use
`http://localhost:8000/docs` to try this endpoint in a browser. See
[Stage 2 notes](docs/stage-2.md) and the
[actual HTTP verification record](docs/stage-2-task-4-verification.json).
Task 4 stops at scanning; it does not chunk, embed or index these files.

## Stage 2 / Task 5: inspect simple code chunks

`POST /api/chunks/debug` scans the selected local repository and splits accepted
files into fixed line windows. It runs without Ollama or Qdrant. It counts all
generated chunks but returns at most `preview_limit` complete chunks (default 5,
range 1..20), so a debug response cannot return an entire large repository.

With the API running, compare a 10-line window with a 2-line overlap:

```powershell
$chunkBody = '{"repository":"demo-repository","chunk_lines":10,"overlap_lines":2,"preview_limit":20}'
$chunkResult = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/chunks/debug `
  -ContentType 'application/json' -Body $chunkBody
$chunkResult | ConvertTo-Json -Depth 8
$chunkResult.chunks | Where-Object { $_.path -eq 'backend/service/RefundService.java' } `
  | Select-Object start_line, end_line, content
```

The measured fixture result is 10 file candidates, 8 accepted files, 2 skipped
files and **18 generated chunks**. The refund file produces ranges **1..10,
9..18 and 17..19**. Each chunk carries `chunk_id`, `repository`, `path`, `language`,
`start_line`, `end_line` and `content`. Line numbers start at 1 and include both
endpoints. Original indentation and line endings are preserved.

The default request `{"repository":"demo-repository"}` uses 60 lines and
10 overlap lines: it generated **8 chunks** for this small fixture and returned
5 previews. `chunks_created` is the total; `chunks_returned` is the preview count,
and `truncated` states whether additional chunks exist. Change `preview_limit`
without changing chunk content or IDs. Empty / whitespace-only files create no
chunks and are listed under `files_without_chunks`; whitespace-only windows are
also skipped without changing their original line numbering.

Configuration defaults (existing `.env` files can rely on these defaults):

```dotenv
CHUNK_LINES=60
CHUNK_OVERLAP_LINES=10
CHUNK_MAX_CHARACTERS=8192
CHUNK_MAX_CHUNKS=10000
```

`0 <= overlap_lines < chunk_lines <= 200` guarantees forward progress. Request
overrides can omit either field to use its environment value; if that combination
is invalid the endpoint returns 422 before scanning. When using a small window,
provide both overrides as in the example. Restart the API after changing settings.

The scanner's per-file limit still applies, and the chunker independently checks
actual text bytes. A line window over the character limit or a repository over the
chunk-count budget returns 413 with no partial success. Reducing `chunk_lines`
can resolve an oversized multiline window; a single oversized line is rejected
rather than silently cut or truncated. Character limits are not tokenizer limits.
The default 8192-character bound matches the current embedding input guard, but
this task does not call that provider.

Run the offline chunking tests:

```powershell
cd services/api
python -m uv run --locked pytest tests/test_chunker.py
```

The Vue application has no chunk-debug page yet; use `http://localhost:8000/docs`
or the request above. See [Stage 2 notes](docs/stage-2.md) and the
[actual chunk verification](docs/stage-2-task-5-verification.json).
Task 5 stops at chunks; embedding and Qdrant index writes belong to Task 6.

## Stage 2 / Task 6: index a local repository

Start Qdrant with `.\infra\Qdrant.ps1 up`, keep Ollama running with
`qwen3-embedding:0.6b` installed, and start or restart the API using the command
below. Use `QDRANT_COLLECTION=repopilot_code`; the earlier three-dimensional
smoke collection is incompatible with the measured 1024-dimensional embeddings.

```powershell
cd services/api
python -m uv run --locked uvicorn app.main:app --reload
```

In another terminal, index the actual files under `workspaces/demo-repository`:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/indexing/repositories/demo-repository
```

No body is required. The name selects one controlled workspace child; window
options come from `CHUNK_LINES` / `CHUNK_OVERLAP_LINES`, not this request.
With the existing 60 / 10 defaults, the real local response includes:

```json
{
  "repository": "demo-repository",
  "files_scanned": 10,
  "supported_files": 8,
  "skipped_files": 2,
  "chunks_created": 8,
  "vectors_indexed": 8,
  "collection": "repopilot_code",
  "embedding_model": "qwen3-embedding:0.6b",
  "embedding_dimension": 1024,
  "strategy": "replace_repository"
}
```

The API also returns `previous_vectors`, active chunk options and scan skips.
`vectors_indexed` is the final exact count for this repository, rather than an
estimated number of attempted writes. Repeat the request: the actual index
remains eight points. Changed or removed files lose their obsolete points.
An existing empty/whitespace-only repository clears its old points; a missing or
unsafe repository fails before embedding or deletion. Files skipped by Scanner
are absent from the replacement index, with reasons reported in the response.

Read the stored data directly from Qdrant (the local default needs no API key):

```powershell
Invoke-RestMethod http://localhost:8000/api/vector-store/collection
$filter = @{ must = @(@{ key = 'repository'; match = @{ value = 'demo-repository' } }) }
$countBody = @{ filter = $filter; exact = $true } | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post `
  -Uri http://localhost:6333/collections/repopilot_code/points/count `
  -ContentType 'application/json' -Body $countBody
$scrollBody = @{ filter = $filter; limit = 3; with_payload = $true; with_vector = $false } |
  ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post `
  -Uri http://localhost:6333/collections/repopilot_code/points/scroll `
  -ContentType 'application/json' -Body $scrollBody
```

You can also open `http://localhost:6333/dashboard`, select `repopilot_code` and
inspect point IDs and payloads, or call the indexing route from the API's
`http://localhost:8000/docs`. This task adds no Vue page.

Indexing prepares all chunks and finite, nonzero vectors before replacing old
points. Embedding and upsert use `EMBEDDING_BATCH_SIZE` (default 16); the provider
keeps its independent timeout. New collections use the actual returned dimension
and Cosine distance. Existing collections must match dimension and the stored
model/provider/endpoint identity; a different identity returns 409 even with the
same dimension. An unknown nonempty collection is not adopted.

The two new preparation budgets are `INDEXING_MAX_TEXT_CHARACTERS=2000000` and
`INDEXING_MAX_VECTOR_VALUES=2000000`. The latter counts numeric coordinates,
not vectors or bytes. Exceeding either returns 413 before repository deletion.
App logs contain repository/count/model/dimension metadata, without source or
credentials. Provider failures return 502, Qdrant failures 503, compatibility or
another active index request 409. Clients close on success and failure.

Replacement is not a transaction: Qdrant failure or process termination after
deletion can leave a partial index. Retry the complete request. Use one API
worker for this prototype; its process-local guard does not coordinate other
workers or external Qdrant writers. Indexing sends source code to the configured
embedding service and stores it as payload. The verified Ollama and Qdrant
services are local; a cloud embedding endpoint would receive those code chunks.

```powershell
cd services/api
python -m uv run --locked pytest -rs
$env:REPOPILOT_INDEXING_INTEGRATION = '1'
python -m uv run --locked pytest tests/test_indexing_integration.py -rs
Remove-Item Env:REPOPILOT_INDEXING_INTEGRATION
```

The opt-in test copies the fixture to a temporary workspace and creates a unique
test collection. It verifies real batches, repeat IDs, removal of obsolete file
points and clearing an empty repository, then removes only its own collection.
It does not modify the demonstration source or persistent `repopilot_code`.
See [Task 6 notes](docs/stage-2.md#task-6-repository-indexing) and
[measured Qdrant data](docs/stage-2-task-6-verification.json).
Task 7 below implements query embedding, repository filtering and TopK vector search.

## Stage 2 / Task 7: search indexed code

Start Ollama, Qdrant and the API as above, and index the target repository before
searching. Restart an API process that was running before this route was added.
Search uses the existing `QDRANT_COLLECTION` and does not scan source files or
create/rebind a collection. Send the repository name, natural-language query and
optional TopK (default 5, strict integer range 1..20):

```powershell
$body = @{ repository = 'demo-repository'; query = '退款逻辑在哪里？'; top_k = 5 } |
  ConvertTo-Json -Compress
$result = Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/retrieval/search `
  -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes($body))
$result.results | Format-Table rank, score, path, start_line, end_line
($result.results | Where-Object path -eq 'backend/service/RefundService.java').content
```

The response contains `repository`, original `query`, requested `top_k`,
collection/model/dimension, `retrieval_latency_ms` and `results`. Each result has
rank, chunk ID, repository, score, relative path, language, inclusive start/end
lines and complete chunk content. Full vectors and extra database payload
fields are omitted. The same embedding identity and actual dimension used for
indexing are checked; search never calls the chat model.

Qdrant receives the repository match filter together with the query vector and
TopK limit, so foreign repositories cannot displace the target's candidates.
TopK limits the number of chunks, not distinct files. There is no score threshold
or reranking. An indexed repository with fewer than K points returns fewer than
K results. A repository name without matching indexed points returns an empty
list when the collection is compatible; search does not require a current disk
directory. A missing collection returns 404, incompatible or missing model
metadata/dimension returns 409, embedding failures 502 and database failures 503.
Invalid names, blank/non-string queries, queries over 8192 characters, invalid
TopK and extra fields return 422 before provider creation. Invalid or foreign
returned code payloads return a safe 502 without partial source results.

The actual local Top5 response for `退款逻辑在哪里？` included:

| Rank | Score | Path | Lines |
| ---: | ---: | --- | --- |
| 1 | 0.48684692 | `README.md` | 1..21 |
| 2 | 0.4854089 | `frontend/src/types.ts` | 1..5 |
| 3 | 0.44492742 | `backend/service/RefundService.java` | 1..19 |
| 4 | 0.44356453 | `frontend/src/RefundPanel.vue` | 1..22 |
| 5 | 0.44177508 | `config/repository.json` | 1..5 |

Top1 returned README; Top3 included the refund implementation; Top20 returned
all eight stored chunks, including lower-scoring material. For
`钱包余额是如何增加的？`, `WalletService.java` ranked first at **0.46036983**.
These are measured observations on the handwritten fixture, not a quality
benchmark. A higher similarity score is not a probability of correctness.

```powershell
cd services/api
python -m uv run --locked pytest -rs
$env:REPOPILOT_RETRIEVAL_INTEGRATION = '1'
python -m uv run --locked pytest tests/test_retrieval_integration.py -rs
Remove-Item Env:REPOPILOT_RETRIEVAL_INTEGRATION
```

The offline tests include foreign vectors more similar than target vectors to
verify filtering before TopK. The opt-in test uses two repositories containing
real fixture code in its own collection, verifies both filters and TopK, then
cleans up only that collection. The persistent eight demonstration points and
source files remain unchanged. Application logs record the JSON-escaped query,
repository, TopK, retrieval latency and result/model metadata; they omit returned
source, vectors and credentials. Each request closes the model and database
clients on success or failure.

Use `http://localhost:8000/docs` to try this endpoint in a browser, or the
Retrieval Debug page below. See [Task 7 notes](docs/stage-2.md#task-7-vector-search)
and [actual search responses](docs/stage-2-task-7-verification.json). Task 7 ends
with retrieved code chunks; evaluation, context injection and LLM answers are
later tasks.

## Stage 2 / Task 8: Retrieval Debug

Start Qdrant with `.\infra\Qdrant.ps1 up`, then start the normal API/web app
with `.\Start-RepoPilot.ps1 -NoBrowser` or the separate development commands
below. Search needs a compatible existing index; run the Task 6 indexing API
first for a new repository. Opening this page never indexes automatically.

Open [Retrieval Debug](http://localhost:5173/#retrieval), or select **Retrieval
Debug** beside **LLM 工作区**. Select `demo-repository`, enter
`退款逻辑在哪里？`, set **TopK** to 5 and click **Search**. Ctrl/Cmd+Enter
also submits. The repository selector initially contains the demonstration name;
to search another indexed repository, type its exact name and press Enter.
It retains submitted names in this page session, rather than discovering the
server's repositories. An unindexed name returns an empty result in a compatible
collection.

The page shows rank, similarity score, full relative path, language, inclusive
line range and expandable complete code. The score label rounds to six decimal
places; hover over it for the raw score. Code is rendered as escaped text,
including Vue/HTML source, with scrolling for long lines/chunks. No complete
vectors are downloaded. Response metadata shows the actual query/repository,
requested TopK, embedding model/dimension and server-measured retrieval latency.
Editing parameters leaves the previous response labelled with its original
request and shows a notice to run Search again.

Search disables duplicate submission and parameter controls while pending.
The page handles initial/empty/loading/error states, has a cancel button and a
180-second client timeout, and maps API errors to readable messages without
displaying raw upstream error bodies. Cancellation stops the browser's wait;
the backend may still finish the read-only request. Clear results preserves the
form. Switching workspaces preserves chat drafts/messages and retrieval state
in memory. The hash selects the workspace on reload; form/results/history are
not persisted across reloads. The four existing LLM modes remain available.

The real browser run found five chunks, with `RefundService.java` at rank 3,
score **0.44492742**, lines **1..19**, using `qwen3-embedding:0.6b` at **1024**
dimensions. README ranked first; TopK=1 consequently missed the implementation.
These are observations on the handwritten fixture, not evaluation metrics.
See [Task 8 notes](docs/stage-2.md#task-8-retrieval-debug-frontend),
[browser verification](docs/stage-2-task-8-verification.json), and
[screenshot](docs/assets/stage-2-task-8-retrieval.jpg).

Frontend contract tests use Node's built-in test runner and the existing
TypeScript compiler, without extra dependencies:

```powershell
cd apps/web
npm test
npm run build
```

Seven tests cover Unicode query limits, inputs, source/score preservation,
empty results, request echoes, repository scope, ranking/duplicate IDs and
malformed results. The full backend suite passed 466 tests (five existing
skips, two existing warnings), and the frontend tests/build passed. Browser
checks covered real Top1/Top5 search, an unindexed repository, state retention
and real Stage 1 streaming. Cancellation and 503 handling were checked with a
separate ephemeral API fixture; the normal backend and Qdrant stayed running.
The small retrieval evaluation is described below.

## Stage 2 / Task 9: measure retrieval

[The dataset](evaluation/datasets/stage2_retrieval.json) contains **12** real
queries grounded in the handwritten fixture's **8** indexed source files.
Each expected path has an actual source snippet, inclusive lines and a file
fingerprint. Ten cases label one file; two cases require two and three files.
Labels were fixed before the run. No model, query, labels, index or retrieval
ranking was tuned after observing results.

Start the existing Qdrant/API with an already indexed `demo-repository`, then
run from the repository root:

```powershell
& '.\services\api\.venv\Scripts\python.exe' '.\evaluation\retrieval\evaluate.py'
& '.\services\api\.venv\Scripts\python.exe' -m pytest '.\evaluation\tests' -q
```

The standard-library runner calls the real search API once per query with
TopK=5 and computes @1, @3 and @5 from the same ranking. It writes a new
timestamped JSON report under `evaluation/reports`; existing reports are
preserved. `--api-base-url`, `--dataset`, `--workspaces`, `--timeout` and
`--output` are available. No indexing, reranking, model answer or LLM judging
is performed.

**Hit@K** is the fraction of queries with at least one expected file among the
first K code chunks. **Macro Recall@K** averages each query's fraction of unique
expected files retrieved. The cutoff is applied to chunks before file
deduplication. Empty results remain misses; errors or changed/stale source abort
the run rather than being omitted from the denominator.

The first real run used `qwen3-embedding:0.6b`, dimension **1024**, on the existing
`repopilot_code` collection:

| K | Hit@K | Macro Recall@K |
| ---: | ---: | ---: |
| 1 | 6/12 (50.00%) | 50.00% |
| 3 | 10/12 (83.33%) | 77.78% |
| 5 | 12/12 (100.00%) | 93.06% |

Both multi-file questions missed `WalletService.java` in Top5: their recalls
were 1/2 and 2/3. Consequently 100% Hit@5 does not mean all relevant files were
retrieved. This is a small learning fixture with only eight chunks; Top5 already
returns five of them. These numbers do not establish production retrieval
quality or answer correctness.

The **32** new offline evaluation tests passed. Dataset/source fingerprints and
all persistent index data matched before/after the real run; the demonstration
still has eight points. Metrics were independently recomputed from the saved
rankings. See [the actual report](evaluation/reports/stage2_retrieval_20261004_01.json),
[verification](docs/stage-2-task-9-verification.json),
[metric definitions and commands](evaluation/README.md), and
[Task 9 notes](docs/stage-2.md#task-9-small-retrieval-evaluation).
Task 10 below completes the Stage 2 test/documentation/acceptance pass.

## Stage 2 / Task 10: final acceptance

The final run on **2026-10-04 (Asia/Shanghai)** used the existing local models,
unchanged demonstration source, fixed dataset and eight persistent Qdrant points.

| Check | Actual result |
| --- | --- |
| Complete default backend suite | 466 passed, 5 skipped; 471 collected |
| Four opt-in real-service integration tests | All four distinct tests passed: three in the initial run, embedding in its corrected targeted rerun |
| Evaluation unit tests | 32 passed |
| Frontend contract tests / production build | 7 passed / TypeScript and Vite passed |
| Twelve real retrieval queries | Hit@1/3/5 = 50% / 83.33% / 100%; Macro Recall@1/3/5 = 50% / 77.78% / 93.06% |
| Real Retrieval Debug page | TopK 5, model/dimension/path/score/lines/code preview checked; no captured console warnings/errors |

Four default skips are opt-in integrations, run separately above. The remaining
skip is Windows file-symlink creation (`WinError 1314`). Two existing dependency
deprecation warnings remain. The first integration run exposed an incorrect
test assumption: identical input text need not produce bit-identical model
vectors. The test now independently computes cosine from the same actual model
responses, with `1e-12` tolerance. No model/network response is stubbed and no
business calculation was changed. See the
[diagnostic](docs/stage-2-task-10-embedding-diagnostic.json).

Install dependencies once with the existing locks; copy `.env.example` only if
`.env` does not already exist. Start Qdrant separately; the Windows app launcher
starts Ollama/API/web but does not start Qdrant or download embedding models:

```powershell
# From the repository root, after dependencies/models are installed:
.\infra\Qdrant.ps1 up
.\Start-RepoPilot.ps1 -NoBrowser
Invoke-RestMethod http://localhost:8000/health/qdrant
```

For a new repository or a deliberate full refresh, index before searching:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/indexing/repositories/demo-repository
$searchBody = @{ repository = 'demo-repository'; query = '退款逻辑在哪里？'; top_k = 5 } |
  ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/retrieval/search `
  -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes($searchBody))
```

Open `http://localhost:5173/#retrieval`, select `demo-repository`, enter the query,
set TopK and click **Search**. Expand a code preview and compare its inclusive
line range with the source. Switch to **LLM 工作区** for the four Stage 1 modes.

Reproduce offline checks and the real evaluation in separate terminals:

```powershell
# Backend terminal, from services/api:
python -m uv run --locked pytest -rs

# Frontend terminal, from apps/web:
npm.cmd test
npm.cmd run build

# Evaluation terminal, from the repository root:
& .\services\api\.venv\Scripts\python.exe -m pytest evaluation/tests -q
$env:PYTHONIOENCODING = 'utf-8'
& .\services\api\.venv\Scripts\python.exe evaluation/retrieval/evaluate.py
```

The evaluator creates a new timestamped report and never overwrites an existing
one. Real-service opt-in flags and the full **28-item acceptance checklist** are
in [Task 10 notes](docs/stage-2.md#task-10-final-tests-documentation-and-acceptance).
The fixed final report is
[stage2_retrieval_20261004_task10.json](evaluation/reports/stage2_retrieval_20261004_task10.json);
the [acceptance record](docs/stage-2-task-10-verification.json) and
[browser screenshot](docs/assets/stage-2-task-10-retrieval.jpg) preserve this run.
The first Task 9 report remains intact. Top5 covers five of only eight fixture
chunks; these metrics do not establish production quality.

## One-click start on Windows

After installing the dependencies below, double-click `Start-RepoPilot.cmd` in
the repository root. It starts the configured local Ollama service (when needed),
the API, and the web app, then opens `http://localhost:5173/`. Running it again
reuses services that are already available. Startup logs are written to
`output/startup/`; the window stays open if startup fails so you can read the
error. If `.env` does not exist yet, the launcher copies `.env.example` to it.

For a command-line check without opening a browser:

```powershell
.\Start-RepoPilot.ps1 -NoBrowser
```

## Start the API

Copy the environment template at the repository root only on first setup:

```powershell
if (-not (Test-Path -LiteralPath '.env')) {
  Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
```

The template defaults to local Ollama with `qwen2.5:0.5b`, so it can be used
without paid API credits. For the official OpenAI endpoint, use
`LLM_PROVIDER=openai`, provide a key and model in `.env`, and leave
`LLM_BASE_URL` empty. For another OpenAI-compatible service, use
`LLM_PROVIDER=openai_compatible` and set its API base URL.

This workspace is configured for the local Ollama service, so it does not use
paid API credits. The local `.env` uses these values:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_API_KEY=ollama
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=qwen2.5:0.5b
```

`LLM_API_KEY=ollama` is only a non-secret placeholder required by the compatible
client. On this machine the Ollama client is installed under
`E:\RepoPilot\Ollama`, and `OLLAMA_MODELS` points to
`E:\RepoPilot\OllamaModels`.

```powershell
cd services/api
python -m uv sync --locked --dev
python -m uv run --locked uvicorn app.main:app --reload
```

The API runs at `http://localhost:8000`. Verify it with:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

Run the backend test suite with:

```powershell
cd services/api
uv run pytest
```

Call the non-streaming chat endpoint with the complete conversation so far:

Task 3 intentionally replaces Task 2's `{ "message": "..." }` body with the
explicit `messages` array; the earlier shape now returns HTTP 422.

```powershell
$body = @'
{
  "messages": [
    {"role": "system", "content": "你是一名编程导师。"},
    {"role": "user", "content": "Spring Boot 的 Service 是什么？"},
    {"role": "assistant", "content": "Service 用于承载业务逻辑。"},
    {"role": "user", "content": "那 Controller 呢？"}
  ]
}
'@
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/api/chat `
  -ContentType "application/json" `
  -Body $body
```

The response contains an assistant message that can be appended directly to the
next request's `messages` array:

```json
{
  "message": {
    "role": "assistant",
    "content": "Controller 接收 HTTP 请求并调用业务层。"
  },
  "model": "configured-model-name"
}
```

### Stream a response

`POST /api/chat/stream` accepts the same `messages` body and returns JSON payloads
framed as Server-Sent Events. `curl.exe -N` disables output buffering so each
event is visible as it arrives:

```powershell
curl.exe -N -X POST http://localhost:8000/api/chat/stream `
  -H "Accept: text/event-stream" `
  -H "Content-Type: application/json" `
  --data-binary '{"messages":[{"role":"user","content":"请解释 SSE。"}]}'
```

A successful stream follows this order:

```text
event: metadata
data: {"model":"configured-model-name"}

event: delta
data: {"content":"SSE"}

event: delta
data: {"content":" 是一种……"}

event: done
data: {"message":{"role":"assistant","content":"SSE 是一种……"},"model":"configured-model-name"}
```

An upstream failure after streaming has begun is sent as a terminal `error`
event. It cannot change the already-started HTTP 200 response into an HTTP 502.
Configuration failures detected before streaming still return the normal HTTP
503 JSON error.

### Analyze a software requirement

`POST /api/analyze` accepts one software requirement and asks the model for a
strict JSON Schema response. Both the request and the model result are validated
with Pydantic:

```powershell
$body = '{"requirement":"为管理后台实现账号密码登录，并限制连续失败次数。"}'
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/api/analyze `
  -ContentType "application/json" `
  -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

The response has exactly four fields:

```json
{
  "summary": "为管理后台实现安全的账号密码登录",
  "difficulty": "medium",
  "tasks": ["设计登录接口", "实现密码校验", "限制连续失败次数"],
  "risks": ["密码存储不安全", "暴力破解"]
}
```

### Ask the tool-calling agent

`POST /api/agent` accepts the same `messages` shape as the chat endpoint. When a
question needs repository information, the model must call the allowlisted
`get_repository_info` tool instead of inventing an answer:

```powershell
$body = @'
{
  "messages": [
    {"role": "user", "content": "当前项目使用什么技术栈？"}
  ]
}
'@
Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/api/agent `
  -ContentType "application/json" `
  -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

The Stage 1 tool deliberately returns fixed demonstration data:

```json
{
  "name": "smart-community",
  "frontend": "Vue 3",
  "backend": "Spring Boot",
  "database": "MySQL"
}
```

It does not read the real repository and cannot run shell commands or arbitrary
Python. Tool names are allowlisted and arguments are validated before dispatch.

## Start the web app

Open another terminal:

```powershell
cd apps/web
npm.cmd ci
npm.cmd run dev
```

The web app runs at `http://localhost:5173`. Its mode selector exposes all four
Stage 1 features:

- **Normal Chat** calls `POST /api/chat` and displays the complete answer.
- **Streaming** calls `POST /api/chat/stream`, appends each SSE `delta` to the
  visible assistant message, then replaces it with the validated `done` message.
- **Structured Output** calls `POST /api/analyze` and renders the validated
  summary, difficulty, tasks, and risks as separate fields.
- **Tool Calling** calls `POST /api/agent`; the backend performs the tool loop and
  the page displays the final assistant answer.

Normal chat, streaming, and tool calling share one multi-turn conversation. The
system message is prepended to each request but is not shown as a chat bubble.
Requirement-analysis input and results are kept separately. Mode switching does
not silently discard either kind of state; use **Clear current result** when a
fresh run is needed. Request and stream errors are shown in the page, and partial
stream text is preserved and marked as failed if streaming stops early.

## Verify Stage 1

The backend tests use deterministic provider stubs and do not consume model
credits or require Ollama to be running:

```powershell
cd services/api
python -m uv run --locked pytest
```

The production frontend build includes TypeScript checking:

```powershell
cd apps/web
npm run build
```

For a real end-to-end check, start Ollama, the API, and the web app, open
`http://localhost:5173`, and submit one request in each of the four modes. The
model name shown by the page should be `qwen2.5:0.5b` when using the supplied
local defaults.

## Current scope

Stage 1 Task 8 completes the LLM test and documentation pass. Stage 2 Task 10
completes tests, documentation and acceptance of the small retrieval dataset,
Hit@K / Macro Recall@K evaluation,
Retrieval Debug frontend, query embedding, filtered TopK code search,
repository indexing, scanning, simple line-based chunking, embedding experiments
and Qdrant infrastructure. Stage 3 Tasks 1–6 add baseline preservation, Tree-sitter
parsers, AST-aware/fallback chunking, a real re-index, retrieval metadata display,
Simple/AST evaluation and an independent bounded Context Builder. Task 7 adds a
single-generation RAG backend and a basic untrusted-context prompt boundary.
Task 8 adds independent Citation validation and authoritative metadata mapping.
Task 9 reviews the prompt/data boundary and adds a limited sensitive-output check.
Task 10 adds the Code Chat page and previews of validated Sources. Task 11 adds
the independent read-only Issue Analyzer. Task 12 records real retrieval,
answer-support and Citation evaluation with explicit source reviews and bad cases.
The API remains stateless: Stage 1 chat callers send the full conversation;
Code Chat takes one repository question and retrieves fresh context.
The Stage 1 tool returns fixed simulated project data; Code Chat and Issue
Analyzer provide no tools. Persistent sessions, streaming RAG and repository-agent
orchestration remain outside the current scope. Stage 3 final functional acceptance
and Stage 4 Tasks 1–10 are complete: Dense baseline, evaluation, candidates,
tokenizer, snapshots, BM25, Exact, Hybrid, Debug integration and optional independent
reranking. Code Chat/Issue Analyzer still use Dense; expansion, Context Selection,
RAG strategy integration and final ablation are later tasks. The current local model's
answer quality has not passed acceptance.
