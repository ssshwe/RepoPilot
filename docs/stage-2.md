# Stage 2: Embedding + Vector Search

Current progress: **Stage 2 / Task 10 complete**. Task 1 baseline checks, Task 2 Qdrant infrastructure and
Task 3 Embedding Provider / debug experiments, Task 4 local repository scanning
Task 5 simple code chunking, Task 6 repository vector indexing, Task 7
repository-filtered TopK vector search, Task 8 Retrieval Debug frontend and
Task 9 small retrieval evaluation and Task 10 tests/documentation/acceptance.
The Stage 2 result is source chunks returned by retrieval. Generating an LLM
answer from them belongs to Stage 3, which has not started.

Read [Task 10 final acceptance](#task-10-final-tests-documentation-and-acceptance)
for the current architecture, concepts, commands and checklist, and the
[Chinese study guide](stage-2-acceptance.md) for answers to the 25 learning
questions. Earlier sections are historical task records: their words such as
"future", "not yet" and measured scores describe that task's original boundary.

## Task 2 responsibilities

| File | Responsibility |
| --- | --- |
| `infra/docker-compose.yml` | Qdrant v1.19.1, loopback HTTP port, named persistent volume, readiness probe |
| `infra/Qdrant.ps1` | Start, inspect or stop Qdrant with Windows Docker or WSL Ubuntu Docker |
| `services/api/app/core/config.py` | Qdrant URL, SecretStr API key, collection name and bounded timeout |
| `services/api/app/retrieval/vector_store.py` | Async SDK client, connectivity, collection creation and compatibility checks |
| `services/api/app/api/vector_store.py` | HTTP health and collection routes |
| `services/api/app/schemas/vector_store.py` | Request and response models; dimension validation |
| `services/api/tests/test_vector_store.py` | Offline configuration, client, collection and HTTP tests |
| `services/api/tests/test_qdrant_integration.py` | Explicitly enabled real-server verification |

No LLM call occurs in this task. No repository file is read or sent to Qdrant.
No vector or payload is written. The collection experiment contains zero points.

## The concepts needed now

**Vector database / Qdrant.** A later task will turn each code chunk into a numeric
vector and store it in Qdrant. Qdrant will compare a query vector with stored
vectors and return candidates plus their metadata. It supplements application
storage; it does not replace the general role of a relational database. Without
this storage layer, RepoPilot would need another mechanism to retain and search
the vectors. Here only its service and collection configuration are implemented.

**Collection.** A named container for points. RepoPilot selects it with
`Settings.qdrant_collection`; request bodies cannot choose an arbitrary name.
`QdrantVectorStore.ensure_collection(dimension)` creates one unnamed vector with
Cosine distance, or verifies an existing collection. A collection is not the same
as a repository: later several repositories may share one collection, with a
repository field in each payload for filtering.

**Dimension.** The number of numeric coordinates in each vector. A vector such
as `[0.1, 0.2, 0.3]` has dimension 3; that is an explanatory example, not model
output. Each vector stored in this collection must have its configured dimension.
The eventual value must come from the selected Embedding Provider. Task 2 requires
an explicit dimension to avoid guessing a model's output size. Different models
may also produce incompatible vector spaces even when their dimensions match.

**Point, vector and payload.** A future Qdrant point will have `id`, `vector` and
`payload`. The vector is used in numerical search. Payload holds the original
code and fields such as repository, path, language and line range, so RepoPilot
can identify the code behind the result. None of those points exist yet in the
Task 2 exercise. Their models and write operations are deferred to later tasks.

**Cosine distance and health.** Cosine is the chosen vector comparison setting;
no similarity calculation or search is performed yet. A high similarity will not
guarantee the code is correct. Separately, process health and database connectivity
answer different questions: `/health` checks the API process; `/health/qdrant`
uses the SDK to list collections, checking both connectivity and credential access.
Qdrant's `/readyz` is the container probe and is accessible even with API-key
authentication enabled, so that probe alone does not validate the backend key.

## Actual request flow

```text
GET /health/qdrant
-> FastAPI get_vector_store dependency
-> Settings from environment / root .env
-> AsyncQdrantClient.get_collections()
-> collection existence flag
-> close client -> HTTP response

POST /api/vector-store/collection {"dimension": 3}
-> Pydantic validation (integer 1..65536; no extra fields)
-> QdrantVectorStore.ensure_collection()
-> existence check -> create if missing
-> read collection metadata -> check Cosine and dimension
-> close client -> Collection response
```

The client is request-scoped and closed on success and failure. Construction
does not make a compatibility network probe. Loopback connections ignore inherited
HTTP proxies. Existing Stage 1 routes do not create a Qdrant client, and the API
can start and serve those routes while Qdrant is stopped.

## API behavior

| Request / condition | Behavior |
| --- | --- |
| `GET /health/qdrant` | 200 and `{status, collection, collection_exists}` when collection-list access succeeds |
| `GET /api/vector-store/collection` | 200 with actual dimension, distance, point count and collection status |
| Collection missing on GET | 404; health can still be 200 |
| `POST /api/vector-store/collection` with `{"dimension": 3}` | Create or validate the configured collection; returns `created` |
| Repeat POST with same dimension | 200, `created: false`; no recreation |
| Existing dimension / distance / named-vector mismatch | 409; preserve data |
| Invalid input | 422 before any collection operation |
| Network / authentication failure | 503 with a bounded error message; omit upstream details and credentials |

Collection creation also handles a concurrent creation conflict by reading and
checking the collection created by the other request. There is no delete endpoint.

## Local startup and persistence

Start with `infra/Qdrant.ps1 up` on Windows, or the direct Compose command in the
README. The Windows helper also supports `status` and `stop`; it uses a systemd
Docker service in WSL when Windows Docker is absent. It keeps that WSL instance
active through a managed hidden process; `stop` releases only the process started
by this helper. The container's readiness
probe makes an HTTP request using bash builtins because the pinned official image
does not include curl or wget. The database files live in a named Docker volume,
not a Windows bind mount. Stopping or restarting the container preserves them.

The default collection `repopilot_code` is left uncreated until the real embedding
dimension is known. Use `repopilot_infra_smoke` for the explicit 3D experiment and
restore the original setting before later indexing. Server and client settings
are separate: the Compose `QDRANT_API_KEY` configures the server key and the same
variable supplies the backend client key. For a remote Qdrant server, supply its
URL and key instead; the local Compose settings do not configure a remote server.

The container has telemetry disabled and receives no LLM credentials. Only the
loopback HTTP port is published. Stage 2 does not need a published gRPC port.
The collection API is a local development administration API, not a production
authentication system.

## Verification

From `services/api`, run `python -m uv run --locked pytest`. The default tests
use mocks and the SDK's in-memory test mode for collections and require neither
Qdrant nor a paid model API. In-memory mode is a testing mechanism; the application
uses the networked Qdrant service.

With Qdrant running, set `REPOPILOT_QDRANT_INTEGRATION=1` and run
`python -m uv run --locked pytest tests/test_qdrant_integration.py`. This checks
real FastAPI-to-Qdrant connectivity and creation / idempotency / mismatch behavior.
It uses a unique test collection and cleans up only that collection. No repository
vectors are created. See README for complete PowerShell commands.

The actual Task 2 run is recorded in
[`stage-2-task-2-verification.json`](stage-2-task-2-verification.json): the default
backend suite passed 100 tests with one opt-in integration test skipped; that
integration test passed separately against Qdrant v1.19.1. The frontend build
passed. A real Uvicorn HTTP run created `repopilot_infra_smoke` with dimension 3,
Cosine distance and zero points, confirmed `created: false` on repetition, and
received 409 for dimension 4. Stopping Qdrant produced database-health 503 while
process-health remained 200. Restarting preserved the collection and its settings.
The three-dimensional collection is an infrastructure experiment, not embedding
model output. The default `repopilot_code` collection remains uncreated.

On this machine WSL could not reach Docker Hub directly. The official pinned
image was downloaded through the existing Windows system proxy, its manifest and
layer SHA-256 digests were verified, and the archive was loaded into WSL Docker.
It is cached there for repeat starts; Compose itself and the readiness probe were
then tested against that image. A different machine still needs registry access
or a verified image import for its first start.

## Task 3 responsibilities

| File | Responsibility |
| --- | --- |
| `services/api/app/embedding/base.py` | Independent `EmbeddingProvider` contract: `embed_text` / `embed_texts` |
| `services/api/app/embedding/provider.py` | Compatible embeddings API, bounded batches, response ordering, client closure and metadata-only logging |
| `services/api/app/embedding/factory.py` | Resolve only `EMBEDDING_*` configuration; create and close request-scoped provider |
| `services/api/app/embedding/validation.py` | Reject empty, nonnumeric, nonfinite, zero, oversized or inconsistent vectors |
| `services/api/app/core/config.py` | Independent embedding model, SecretStr key, URL, timeout and batch size |
| `services/api/app/retrieval/similarity.py` | Independently tested, numerically stable cosine function |
| `services/api/app/schemas/embeddings.py` | Strict text requests; bounded vector preview and score responses |
| `services/api/app/api/embeddings.py` | Preview and two-text similarity endpoints |
| `services/api/tests/test_embeddings.py` | Offline provider, configuration, mathematics, lifecycle and API tests |
| `services/api/tests/test_embedding_integration.py` | Explicit opt-in for real configured model calls |

## Task 3 concepts in the code

**Embedding and provider.** `EmbeddingProvider.embed_text("订单退款")` converts
that string into a numeric vector using an already trained model. This is
inference, not training. `LLMProvider` generates text and manages conversation;
`EmbeddingProvider` produces numbers. They use separate classes and environment
settings even though both can communicate through a compatible HTTP API. The
current local chat model is `qwen2.5:0.5b`; embedding uses `qwen3-embedding:0.6b`.
See the [official model entry](https://ollama.com/library/qwen3-embedding:0.6b)
and [embedding guide](https://docs.ollama.com/capabilities/embeddings).

**Vector and dimension.** `vector` is a list of floating-point coordinates,
not a list of keywords or human-readable semantic labels. The debug route
reports `len(vector)` as the dimension and `vector[:8]` as the preview. No model
dimension is guessed or forced in the SDK request. A preview lets you inspect
the shape without sending thousands of numbers to a UI; it is insufficient for
computing the real similarity, which uses the complete vectors in the backend.
Future indexing and query embedding must use the same model and preprocessing.
Equal dimensions alone do not make two different models' vectors compatible.

**Cosine similarity.** `cosine_similarity(a, b)` computes
`sum(a_i * b_i) / (norm(a) * norm(b))`: it compares directions rather than vector
lengths. Same direction gives 1, perpendicular gives 0 and opposite gives -1.
The implementation rescales finite coordinates before computing norms to avoid
overflow / underflow, then clamps floating-point roundoff into `[-1, 1]`.
Zero vectors have no defined direction and are rejected instead of assigned a
fake score. A higher score means this model sees the inputs as closer; it is
neither a probability nor proof that a future code result is correct. The usual
cosine distance is `1 - similarity`; these endpoints return similarity.

**Batching and vector integrity.** `embed_texts` validates all inputs first,
then splits them into requests of at most `EMBEDDING_BATCH_SIZE` (default 16,
range 1..128). Each result is reordered by the provider's `index`; count,
unique indexes, dimension and finite numeric values are checked before returning.
Mixed dimensions across separate batches also fail. Text whitespace is preserved,
including indentation needed by later code indexing. An empty input list produces
no request. HTTP texts are limited to 8192 characters; this is an application
size guard, not a tokenizer or a guarantee of fitting every model's token limit.

**Configuration and lifecycle.** The factory reads only `EMBEDDING_*` values.
Blank model, key or URL yields a safe 503 when an embedding endpoint is called;
Stage 1 and `/health` remain available. Invalid HTTP bodies fail with 422 before
constructing a provider. The request-scoped context closes its async SDK client
on success and failure. Each upstream request has a bounded timeout (default
120 seconds) and no automatic retry; total time for many batches can exceed one
request timeout. Loopback calls bypass inherited HTTP proxy variables. Failures
and malformed upstream vectors produce 502 without upstream bodies or credentials.
Application logs include model, dimension, count, batch size and latency, not text
or keys. See [Ollama's compatible API documentation](https://docs.ollama.com/api/openai-compatibility).

## Task 3 data flow

```text
POST /api/embeddings/debug {"text": "订单退款"}
-> Pydantic text validation
-> EMBEDDING_* settings -> EmbeddingProviderFactory
-> embed_text -> embed_texts([text])
-> OpenAI-compatible /embeddings with model + input + float encoding
-> order and validate complete vector -> actual dimension + first 8 values
-> close provider -> HTTP response

POST /api/embeddings/similarity {"text_a": "订单退款", "text_b": "退还订单金额"}
-> same text validation and independent provider
-> embed_texts([text_a, text_b]) -> complete vectors
-> cosine_similarity -> model + dimension + score
-> close provider -> HTTP response
```

No Qdrant client is constructed by these routes. No collection is created and
no vector is stored. The default `repopilot_code` remains uncreated in Task 3.

## Task 3 measured results and verification

The real HTTP run is recorded in
[`stage-2-task-3-verification.json`](stage-2-task-3-verification.json). The model
ran locally in Ollama 0.34.0. The official registry's 639150592-byte model blob
was downloaded through the existing Windows system proxy, verified against
SHA-256 `06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439`,
uploaded through Ollama's local blob API and registered by pulling the official
model manifest. Normal setup on another machine is still `ollama pull qwen3-embedding:0.6b`.

For the actual input `订单退款`, `POST /api/embeddings/debug` returned HTTP 200:

```json
{
  "model": "qwen3-embedding:0.6b",
  "dimension": 1024,
  "preview": [
    -0.034106042, 0.020083917, -0.0065465816, -0.10679022,
    0.012906836, -0.036488317, -0.020054916, -0.04261014
  ]
}
```

| Actual text pair | Measured cosine similarity |
| --- | ---: |
| 订单退款 / 退还订单金额 | 0.8017290049765894 |
| 订单退款 / 今天天气不错 | 0.33420674085257857 |
| 订单退款 / 订单退款 | 1.0 |

These are measured results for this model and these inputs, not a universal
threshold or an evaluation of code retrieval. The related pair scored higher
than the unrelated pair in this experiment. A nonzero unrelated score is possible;
embedding vectors are not a boolean match function. Different versions, precision
or input phrasing can change the numbers.

The offline backend suite passed **182 tests**, with the two opt-in integrations
skipped and two existing deprecation warnings. The new offline embedding module
accounts for 82 tests. The real embedding integration passed separately (1 test),
and the frontend TypeScript / Vite build passed. The real HTTP run also confirmed
blank input returns 422, process health returns 200, and Qdrant health returns
200 with `repopilot_code` still absent. No repository or vector writes occurred.
See README for the UTF-8 PowerShell requests, model pull and opt-in test commands.

## Task 4 responsibilities

| File | Responsibility |
| --- | --- |
| `services/api/app/repository/scanner.py` | Traverse one repository, apply limits, read valid text and compute real results |
| `services/api/app/repository/paths.py` | Portable repository names, containment, link rejection and bounded file reads |
| `services/api/app/repository/ignore.py` | Fixed directory and filename ignore rules |
| `services/api/app/repository/language.py` | Supported extension-to-language mapping |
| `services/api/app/repository/models.py` | Internal source files with text and scan / skip results |
| `services/api/app/schemas/repositories.py` | Validated request and public metadata-only response |
| `services/api/app/api/repositories.py` | `POST /api/repositories/scan`; sync disk reads in the FastAPI worker pool |
| `services/api/app/core/config.py` | Bounded per-file, entry and total accepted byte settings |
| `services/api/app/core/exceptions.py`, `services/api/app/main.py` | Register scanner routes and safe 403 / 404 / 413 responses |
| `services/api/tests/test_repository_scanner.py` | Path, link, encoding, ignore, resource limit and API verification |
| `workspaces/demo-repository/` | Hand-written local code fixture for real scanning and later tasks |

## Task 4 concepts in the code

**Scanner versus chunker.** `RepositoryScanner.scan("demo-repository")` discovers
eligible files and returns a `RepositoryScanResult`. Each internal `RepositoryFile`
has repository, relative path, language, raw byte size, line count and original
decoded text. One file is still one file here, not a code chunk. The later
chunker will split that text and assign line ranges. No parser, embedding model,
database or repository code execution is involved in the scan.

**Path boundary.** The public request accepts only a single ASCII repository
name (1..64 characters; letters/digits followed by letters, digits, `_`, `.` or
`-`). It rejects separators, drive / UNC paths, traversal, Windows device names
and trailing dots. The server constructs its fixed root from the RepoPilot
directory, so the API working directory does not change the boundary. Real
containment uses path components, not a string prefix such as `workspaces-extra`.
Repository roots and all child components are checked for symlinks and Windows
reparse points, including junctions. Reparse entries are never traversed.

Before reading bytes, Windows also checks the actual opened handle's final path
against the repository root. POSIX opens every ancestor directory component and
the file with `O_NOFOLLOW`, using directory descriptors. File identity, byte size
and modification time checks detect replacement or modification during a read.
No filesystem snapshot is taken: concurrent edits can produce skip records and
separate accepted files can come from different moments. The API is a local
development tool; it does not add an authentication or multi-user sandbox system.
See [Python file APIs](https://docs.python.org/3.12/library/os.html),
[reparse-point flags](https://docs.python.org/3.12/library/stat.html) and
[Windows final path by handle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfinalpathnamebyhandlew).

**Ignore rules and language detection.** `ignore.py` prunes `.git`,
`node_modules`, `dist`, `build`, `target`, `.idea`, `.vscode`, `__pycache__`,
`.venv`, `coverage` and several tool caches before descending into them. It
also skips environment/key files, lock files and minified JavaScript. These are
fixed rules, not a `.gitignore` parser. `language.py` checks only the final file
extension against the ten supported extensions; it does not inspect an AST or
assume that an extension proves a file contains valid source code.

**Text and byte limits.** Only regular supported files are opened, with a
maximum read of `REPOSITORY_MAX_FILE_BYTES + 1`. A supported `.py` containing
NUL / other binary control bytes is skipped as `binary`; non-UTF-8 is
`invalid_encoding`. UTF-8 BOM is removed during decoding; indentation, CRLF and
trailing newlines remain in the internal content. `size_bytes` is the raw on-disk
size including BOM, not Python string length. `line_count` is
`len(content.splitlines())`, including zero for an empty file. Empty text is
accepted now; the later chunker must produce no empty chunks.

Defaults are 1 MiB per file, 10000 visible traversal entries and 20 MiB of total
accepted source bytes. Individual oversized files are skipped. Exceeding either
whole-scan budget aborts with 413; no partial successful count is returned.
Directories count toward the entry limit, including ones pruned at that level.
Their descendants do not. Limits keep accepted source content and metadata
bounded; they are separate from a later embedding model's token limit.

**Honest statistics and metadata.** The response projects internal files into
metadata, deliberately omitting `content`. Counts are computed from results:
`files_scanned = supported_files + skipped_files`, and `total_bytes` sums raw
bytes of accepted files. `files_scanned` counts file candidates in visited
directories, including skipped links / special entries; ordinary directories
are separate. A directory link is reported as an `unsafe_link` skipped candidate
without examining its target. Ordinary ignored / inaccessible directories appear
in `pruned_directories`; their descendants are not included in the file counters.
Thus this is not a total count of every file physically present under a repository.
Every returned path is relative to the selected repository and uses `/`.

## Task 4 data flow and API behavior

```text
POST /api/repositories/scan {"repository": "demo-repository"}
-> Pydantic repository-name validation (no extra fields)
-> fixed RepoPilot/workspaces root + configured resource limits
-> reject unsafe repository root
-> bounded iterative directory traversal -> prune ignored directories
-> candidate file checks -> extension / bounded read / binary / UTF-8 validation
-> internal source files + skip records
-> actual counts + relative metadata -> HTTP response
```

| Condition | Result |
| --- | --- |
| Successful scan, including an empty repository | 200 with actual counts and lists |
| Invalid name, non-string repository or extra request fields | 422 before constructing the scanner |
| Missing repository directory | 404 without an absolute path |
| Unsafe / inaccessible repository root | 403 with a safe error |
| Individual invalid / unreadable file | Skip entry with reason; other files continue |
| Whole-scan entry / accepted byte budget exceeded | 413, no partial success response |

The sync route runs in FastAPI's worker pool so disk reads do not block its async
event loop. The scanner logs only repository name, counts and byte totals, never
source text or absolute paths. Repeat scans do not write files or cache results.
The Stage 1 repository-information tool still returns its original demonstration
data; it is not silently connected to this scanner.

## Task 4 measured results and verification

The hand-written fixture is an actual local directory with ten files. It is not
an imported production repository or a full deployed refund application. The
real Uvicorn HTTP response is recorded in
[`stage-2-task-4-verification.json`](stage-2-task-4-verification.json):

```json
{
  "repository": "demo-repository",
  "files_scanned": 10,
  "supported_files": 8,
  "skipped_files": 2,
  "total_bytes": 4076
}
```

One accepted real file is:

```json
{
  "path": "backend/service/RefundService.java",
  "language": "java",
  "size_bytes": 554,
  "line_count": 19
}
```

The accepted files comprise Java (3), JSON (1), Markdown (1), SQL (1),
TypeScript (1) and Vue (1). `notes.txt` is `unsupported_extension` and
`package-lock.json` is `ignored_file`. This fixture has no pruned directories;
the temporary-directory tests verify all fixed directory ignore rules separately.
Repeated real HTTP scans returned identical results and before / after hashes
confirmed all fixture source files were unchanged. Traversal / path overrides
returned 422, a missing repository returned 404 and `/health` returned 200.

The full backend suite passed 277 tests, with three skips: the two existing
opt-in service integrations and one file-symbolic-link case unavailable on this
Windows machine. The scanner contributes 95 passing cases and that one skip;
actual directory junction, loop, repository-root and opened-file containment
checks passed. The TypeScript / Vite frontend build also passed. The two existing
deprecation warnings remain. See README for startup, requests and test commands.

## Task 5 responsibilities

| File | Responsibility |
| --- | --- |
| `services/api/app/indexing/models.py` | Immutable `CodeChunk` with ID, provenance, inclusive line range and content |
| `services/api/app/indexing/chunker.py` | Fixed line windows, overlap, whitespace filtering, resource limits and deterministic UUIDs |
| `services/api/app/schemas/chunks.py` | Validated debug options, complete chunk previews and file-level counts |
| `services/api/app/api/chunks.py` | Scan / chunk debug route, count all output but keep bounded preview memory |
| `services/api/app/core/config.py` | Chunk defaults, limits and overlap relationship validation |
| `services/api/app/core/exceptions.py`, `services/api/app/main.py` | Chunk limit error / HTTP 413 and route registration |
| `services/api/tests/test_chunker.py` | Source reconstruction, inclusive ranges, overlap, IDs, limits and HTTP behavior |

The `indexing` package currently contains only models and the chunker. There is
no indexer, embedding orchestration, payload write or repository deletion yet.
The scanner remains the only filesystem-reading component used by this route.

## Task 5 concepts in the code

**Chunk.** `SimpleCodeChunker.chunk_file(RepositoryFile)` turns one accepted
file into smaller `CodeChunk` values. A chunk is the future unit of embedding
and retrieval, so its text should be small enough to process while retaining
useful surrounding code. Each chunk keeps the original repository, relative file
path, language, start / end lines and exact decoded text. A file is not a vector,
and a chunk is not a Qdrant point in this task.

**Line ranges.** The implementation uses
`content.splitlines(keepends=True)` and joins a window without normalizing or
trimming it. This uses the same line-boundary convention as the scanner's
`splitlines()` count, including CRLF, CR and Unicode line separators. Ranges
are one-based and inclusive: the Python slice `lines[start:end]` corresponds to
`start_line = start + 1`, `end_line = end`. A final newline does not invent an
extra empty line. Keeping provenance makes later source citations possible.

**Overlap.** Defaults are a 60-line window and 10 shared lines. The start
advances by `chunk_lines - overlap_lines`, which must be positive. In the actual
10 / 2 experiment, the refund file windows are 1..10, 9..18 and 17..19. Neighboring
windows share two lines, giving code near a boundary context on both sides.
Overlap also duplicates text, so later it increases embedding and storage work.
This chunker knows line boundaries, not functions or classes; a window can cut
through a method. AST-aware chunking remains outside Stage 2.

**Empty windows and limits.** Empty or whitespace-only files create no chunks.
A whitespace-only candidate window is skipped; other windows retain their
original start / end positions and leading / trailing blank lines. Iteration
stops as soon as a window reaches the file end, avoiding an additional suffix
that contains only already-covered overlap. The chunker rechecks raw metadata
size and actual UTF-8 text bytes against the scanner's file limit even when
called directly. Each nonblank window must fit `CHUNK_MAX_CHARACTERS` (default
8192); `CHUNK_MAX_CHUNKS` (default 10000) bounds both single-file materialization
and the full debug scan. Exceeding a limit raises an error rather than dropping
source text or returning partial success. These are byte / character / count
guards, not token counts. The 8192 default matches the current embedding text
guard; changing it does not change the provider's limit.

**Stable identity.** The chunk ID is a valid UUID v5. Its deterministic input
includes the chunker version, repository, path, language, inclusive range, window
configuration and a SHA-256 hash of its exact content. Same inputs give the same
ID, even when the debug preview limit changes. Different files, ranges, text or
chunking options give different IDs. Stability does not implement reindexing by
itself: changed chunks can have new IDs, and handling stale database points is
Task 6 work. Nothing is written to Qdrant now.

## Task 5 data flow and API behavior

```text
POST /api/chunks/debug
{"repository":"demo-repository","chunk_lines":10,"overlap_lines":2,"preview_limit":20}
-> validate name / strict integer options / overlap
-> resolve environment defaults and request overrides
-> existing safe RepositoryScanner -> accepted RepositoryFile texts
-> SimpleCodeChunker.iter_file_chunks -> complete CodeChunks
-> validate all output / count all chunks / retain only requested previews
-> actual per-file counts + provenance + complete preview content -> HTTP response
```

Request window options can be omitted to use `CHUNK_LINES` and
`CHUNK_OVERLAP_LINES`; an invalid combination of one override with the other
default returns 422 before disk reads. Preview range is 1..20 with default 5.
`chunks_created` counts all generated output, `chunks_returned` counts the
preview list and `truncated` reports whether previews omit later chunks.
Preview truncation never trims a returned chunk's content. The route continues
checking later chunks even after the preview is full; a later limit error still
returns 413. Internal iteration does not retain all generated chunk texts in
memory. Every supported file appears in `files` with its actual chunk count;
zero-output paths appear in `files_without_chunks`.

| Condition | Result |
| --- | --- |
| Valid repository, including empty / whitespace-only files | 200 with real counts and bounded complete previews |
| Invalid name, extra fields, noninteger / out-of-range options or overlap mismatch | 422 before scanning |
| Missing repository | Existing scanner 404 |
| Unsafe / inaccessible repository root | Existing scanner 403 |
| File skipped by Scanner (including oversized source) | Report existing skip reason; no chunks from that file |
| Chunker file / character / count limit exceeded | 413; no partial success |

This is a local read-only debug endpoint. Source content is returned only in
bounded chunk previews; it is not logged, executed, sent to an embedding service
or persisted. There is no new Vue page; use Swagger or PowerShell for this task.

## Task 5 measured results and verification

The actual local Uvicorn run is saved in
[`stage-2-task-5-verification.json`](stage-2-task-5-verification.json).

| Request configuration | Generated chunks | Returned previews | Truncated |
| --- | ---: | ---: | --- |
| Default 60 lines / 10 overlap / preview 5 | 8 | 5 | true |
| 10 lines / 2 overlap / preview 20 | 18 | 18 | false |
| 10 lines / 2 overlap / preview 1 | 18 | 1 | true |

The 10 / 2 run scanned ten candidates, accepted eight files, skipped two and
created chunks for all eight accepted files. The refund file's ranges were
1..10, 9..18 and 17..19. Its last actual chunk was:

```json
{
  "chunk_id": "16a717be-c3fe-50b1-ae82-e3faf617578a",
  "repository": "demo-repository",
  "path": "backend/service/RefundService.java",
  "language": "java",
  "start_line": 17,
  "end_line": 19,
  "content": "        wallets.credit(userId, amount);\n    }\n}\n"
}
```

Every returned custom-preview content was compared with its source file's exact
line slice. Refund overlap was compared line by line. Repeat responses and IDs
were identical, source hashes remained unchanged, and changing the preview limit
did not change the total. Invalid overlap / repository path returned 422,
a missing repository returned 404 and process health returned 200.

The new chunking module passed 69 offline tests. The complete backend suite
passed **346 tests** with the same three skips (two opt-in integrations and
Windows file-symbolic-link creation requiring privileges) and two existing
deprecation warnings. The frontend TypeScript / Vite build passed. Tests also
verify empty input, blank windows, exact line endings, stable IDs, per-file and
whole-repository limits, and no embedding / Qdrant calls. See README for requests
and commands.

## Task 5 boundary

Task 6 connects Scanner -> Chunker -> Embedding -> Qdrant, adds the repository
index API and handles basic repeated indexing. Task 5 stops before embedding
repository chunks or writing points. Later indexing
sends code chunks to the embedding provider. If it uses a cloud
API, those code chunks leave the local environment for the external model service;
running Qdrant locally does not make cloud embeddings local.

Point payloads, indexing, repository filtering, TopK retrieval,
Retrieval Debug and evaluation have not been implemented. No context injection,
RAG answer, Tree-sitter, BM25, hybrid search or reranker is added in Task 5.

## Task 6: repository indexing

| File | Responsibility |
| --- | --- |
| `services/api/app/api/indexing.py` | Validate the route name, prepare local inputs in a thread pool, own and close model/DB clients |
| `services/api/app/indexing/service.py` | Bound chunk preparation, embed and validate batches, replace one repository, verify its final exact count |
| `services/api/app/schemas/indexing.py` | Counts, active configuration and scan skips; no source content or complete vectors in the index response |
| `services/api/app/retrieval/vector_store.py` | Collection model binding, exact repository counts, filtered deletion and confirmed upsert |
| `services/api/app/core/config.py` / `.env.example` | Total prepared text and vector-coordinate budgets |
| `services/api/app/main.py` / `core/exceptions.py` | Route, safe 409/413 mappings, app metadata logs enabled in normal Uvicorn startup |
| `services/api/tests/test_indexing.py` | Offline pipeline, real in-memory SDK points, failure ordering, stale removal, isolation and resource checks |
| `services/api/tests/test_indexing_integration.py` | Opt-in local embedding + real Qdrant test with its own collection and copied fixture |
| `docs/stage-2-task-6-verification.json` | Real HTTP responses, read-back point metadata/vector preview and source hashes |

### Data flow

```text
POST /api/indexing/repositories/{repository_name} (no body)
-> validate portable workspace name
-> acquire the single API process's indexing guard
-> Scanner -> accepted RepositoryFiles and scan statistics
-> Chunker -> complete CodeChunks, bounded count/text and embedding input lengths
-> EmbeddingProvider -> batches of EMBEDDING_BATCH_SIZE
-> validate count, ordering, finite/nonzero values and one common dimension
-> ensure Cosine collection with the measured dimension and compatible model identity
-> exact count of previous points for this repository
-> delete points matching payload.repository, wait for completed
-> batch upsert UUID + vector + source payload, wait for completed
-> exact repository count must equal chunks_created
-> actual index response and metadata log -> close clients and release guard
```

Scanning/chunk preparation runs in FastAPI's worker thread pool, keeping the
event loop available for health requests. Embedding and DB I/O are asynchronous;
there is no queue or background job. One process-local nonblocking lock spans
preparation and writing. A competing index request receives 409; other endpoints
remain available. Use a single API worker. This lock does not coordinate
multiple processes or external writers, and a remote operation can still finish
after a network timeout or request cancellation.

### Concepts and choices

**Indexing.** Read accepted source, divide it into chunks, generate semantic
vectors and persist the vectors together with provenance. Later search can read
these points without embedding the whole repository again. Task 6 does no query
embedding or similarity search and does not ask an LLM to generate an answer.

**Point and payload.** Each Qdrant point has a stable UUID, one unnamed vector,
and a JSON payload. Payload contains `repository`, relative `path`, `language`,
inclusive `start_line` / `end_line`, complete `content`, `chunk_id`, embedding
model, schema version and chunk options. Vector coordinates support numerical
comparison; payload lets the application locate and display the original code.
The `repository` field scopes deletion/counting now and will scope search in
Task 7. Source is persisted in Qdrant and not included in API index responses
or application logs.

**Compatibility.** The collection dimension comes from actual vectors. Every
batch must have the same dimension. The collection also stores
`metadata.repopilot_index`, containing schema version, provider/model names and
a SHA-256 fingerprint of the normalized configured embedding endpoint. This
contains no key and does not persist the URL. An empty unbound collection can
be adopted while retaining other metadata; a nonempty unbound collection is
rejected. A bound collection requires the same identity, even after its points
are cleared. A new model or endpoint requires a different collection. Equal
dimensions alone do not establish a shared vector space. The configuration
fingerprint cannot detect an artifact changing behind the same model/service
name; keep the model artifact fixed. Future query embedding must verify this
same identity.

**Repeat indexing.** The selected strategy is `replace_repository`. Since chunk
IDs include content and options, upsert alone would leave stale IDs after file
edits/deletions or changed chunking options. All embeddings are prepared and
validated first. Then a mandatory `repository` filter deletes only the current
repository's points, followed by batch upserts. Other repository points remain.
The final exact repository count, rather than a loop counter, becomes
`vectors_indexed`. `previous_vectors` records the count before replacement.
The SDK operations use `wait=True` and require a `completed` result; these
semantics follow the official [upsert](https://api.qdrant.tech/api-reference/points/upsert-points)
and [filtered deletion](https://api.qdrant.tech/api-reference/points/delete-points)
APIs. Metadata uses [collection update](https://api.qdrant.tech/api-reference/collections/update-collection).

**Batching and bounded preparation.** `EMBEDDING_BATCH_SIZE` controls both
embedding and point write batches. The independent provider timeout still
applies to each embedding call. Count/order/vector validation from Task 3 is
reused, with an additional check across indexing calls. Preparation holds vectors
in memory so model/config failures happen before old points are removed.
`INDEXING_MAX_TEXT_CHARACTERS` bounds total chunk characters, including overlap,
and `INDEXING_MAX_VECTOR_VALUES` bounds `chunks_created * dimension`; both
default to 2,000,000 and can be set up to 20,000,000. Coordinates are not bytes:
Python lists/floats need more memory than raw float32 arrays. Existing scan,
file/chunk/count guards also apply. A larger chunk character configuration does
not increase the provider's 8192-character input limit.

### Behavior and failure boundary

| Condition | Result |
| --- | --- |
| Valid source and compatible model/collection | 200 with final exact repository point count |
| Invalid portable name or overlong route value | 422 before filesystem/provider access |
| Missing repository | 404 before providers or deletion |
| Unsafe/inaccessible repository | Existing 403 |
| Source/text/chunk/vector preparation limit | 413 before repository deletion |
| Missing embedding configuration | 503 before Qdrant client creation |
| Embedding failure, invalid vectors or dimensions changing between batches | 502; existing points preserved |
| Incompatible dimension, model identity or nonempty unowned collection | 409; existing points preserved |
| Another process-local indexing request is active | 409; retry when it finishes |
| Qdrant failure before deletion | 503; existing points preserved |
| Unconfirmed delete/upsert/final exact count | 503 with a safe partial-index message; retry full index |
| Existing empty/blank/only-skipped repository and absent collection | 200 with zero counts, null dimension, no embedding calls or collection creation |
| Existing repository producing zero chunks with a compatible existing collection | Delete its old points, confirm count zero; preserve other repositories |

Even zero-chunk requests require valid configured embedding settings; they make
no embedding network call. A missing directory is not interpreted as an empty
repository and does not clear anything. Scanner-skipped files are intentionally
absent from the replacement index. Scan skip details remain visible.

Deletion and upsert are separate operations. There is no transaction/rollback:
failure, cancellation or process termination after deletion can leave no points
or a partial index. This prototype reports unconfirmed writes as failure and
supports rerunning the full request. Generation switching, incremental indexing,
background tasks, distributed locks and concurrent readers' consistent snapshots
remain future work.

App INFO logs are enabled during normal API startup. Completion records
`repository`, `files_scanned`, `chunks_created`, `embedding_model`,
`embedding_dimension`, `vectors_indexed` and `collection`; failures after
replacement begins log only repository/collection/chunk count. No source or
credentials are logged. Source chunks go to the configured embedding service
and full content is stored in the local Qdrant volume. Local Ollama keeps the
verified embedding calls local; choosing a cloud provider sends source there.

### Actual results and acceptance

The existing handwritten fixture is the actual input, rather than production
application code. Default 60-line / 10-overlap indexing scanned 10 candidates,
accepted 8 files, skipped `notes.txt` and `package-lock.json`, created 8 chunks
and confirmed **8 actual points** in `repopilot_code`. The measured dimension
was **1024**, using `qwen3-embedding:0.6b` with Cosine. Repeat indexing still had
8 points and identical UUIDs. The saved HTTP runs started with an already
indexed fixture (`previous_vectors: 8`); this value is measured, not a claim
that the database was initially empty.

Direct Qdrant filtered count and scroll verified all eight stored vectors and
payloads, with exact source-line comparisons and unchanged source hashes. The
actual refund point was UUID `67881397-a785-5ed6-ad27-40ce2d67e8e6`, path
`backend/service/RefundService.java`, language `java`, range **1..19**, vector
length 1024. Its stored first eight coordinates were:

```json
[-0.030304136, 0.071053244, -0.007012965, -0.03631072,
 0.0369386, -0.10422923, -0.008012391, -0.023612952]
```

Qdrant normalizes vectors in a Cosine collection during upload, so read-back
coordinates may differ from the raw embedding output while preserving their
direction. See the official [collection distance behavior](https://qdrant.tech/documentation/manage-data/collections/).
The complete measured sample payload and bounded vector preview are in
`stage-2-task-6-verification.json`; full vectors are not copied into that document.

The new module passed **42 offline tests**. The opt-in real-server test passed
with a copied fixture and a unique collection: 10 / 2 chunking produced 18
points through 16 + 2 embedding/write batches, a repeat preserved all 18 IDs,
removing README reduced the count to 15, and clearing source files reduced it
to zero. Cleanup removed only that unique test collection. The persistent
demonstration remains eight points. The complete backend suite passed **388
tests**, with four skips: the three opt-in external-service tests and the
existing Windows file-symbolic-link privilege limitation. The new indexing
integration was separately enabled and passed; two existing deprecation warnings
remain. Frontend TypeScript / Vite build passed.
See README for API startup, PowerShell count/scroll, Swagger, Qdrant dashboard
and pytest commands.

## Task 6 boundary

Task 7 implements `/api/retrieval/search`: query embedding, compatible collection,
repository filter and TopK scored code chunks. Task 6 stops after verified index
storage. Vector search, Retrieval Debug UI, Hit@K / Recall@K evaluation, context
injection, RAG answers, AST chunking, BM25, hybrid search and reranking remain
unimplemented.

## Task 7: vector search

| File | Responsibility |
| --- | --- |
| `services/api/app/api/retrieval.py` | `POST /api/retrieval/search`, validated input and request-owned provider/DB clients |
| `services/api/app/schemas/retrieval.py` | Strict query/repository/TopK validation, bounded full code results and source line range validation |
| `services/api/app/retrieval/service.py` | Verify model compatibility, embed one query, project scored points, reject malformed/foreign results and log query/latency |
| `services/api/app/retrieval/vector_store.py` | Read-only collection compatibility check and Qdrant `query_points` with mandatory repository filter |
| `services/api/app/embedding/identity.py` | Shared index/query schema/provider/model/endpoint fingerprint, moved from indexing service without changing its identity |
| `services/api/app/indexing/service.py` | Reuse the shared identity; existing indexing behavior is preserved |
| `services/api/app/main.py` / `core/exceptions.py` | Retrieval route and safe invalid-result 502 mapping |
| `services/api/tests/test_retrieval.py` | Offline real in-memory Cosine search, repository isolation, TopK, compatibility, validation and failure cleanup |
| `services/api/tests/test_retrieval_integration.py` | Opt-in model + Qdrant test with two temporary repositories and a unique collection |
| `docs/stage-2-task-7-verification.json` | Actual search requests/responses, rankings, scores and read-only data hashes |

### Data flow

```text
POST /api/retrieval/search {repository, query, top_k?}
-> validate portable repository name / nonblank query <=8192 chars / strict TopK 1..20
-> independent EmbeddingProvider and configured Qdrant client
-> read existing collection config: unnamed Cosine vector and matching index identity
-> embed_texts([query]) -> validate exactly one finite, nonzero query vector
-> check actual query dimension against the collection
-> Qdrant query_points(query=vector, filter=repository equality, limit=top_k)
-> ranked scored points, requested payload fields, no stored vectors
-> validate repository/model/schema/UUID/path/line range/content for every result
-> response: score/path/language/lines/full content + request/model/latency metadata
-> log JSON-escaped query and metadata -> close both clients
```

Search is read-only. It does not scan source, execute code, adopt or create a
collection, delete/upsert points, or call the Stage 1 LLM. A query is sent to the
configured embedding service just as chunks are during indexing. The verified
service is local Ollama. Search reads stored content; disk source changes remain
invisible until that repository is reindexed.

### The concepts in this implementation

**Query embedding and compatibility.** `RetrievalService` converts the query
into a vector using the independent embedding abstraction. It shares
`embedding_identity` with indexing, checking provider, model, schema and
endpoint fingerprint before the model call, then checking the actual output
dimension. Same dimension with a different model/endpoint is rejected. No
collection metadata is changed to make a request fit. The fingerprint detects
configuration changes, not a model artifact changing behind the same name.

**Repository filter.** `QdrantVectorStore.search_repository` constructs a
mandatory `Filter(must=[FieldCondition(key="repository", match=...)])` and passes
it in the same server request as the vector and TopK. Filtering after a global
TopK would let a more similar foreign repository displace target candidates.
The test deliberately creates that situation and verifies that target chunks
still fill the requested limit. Repository names match exactly, including case;
`demo`, `Demo` and `demo-extra` are separate payload values. Projection also
rejects an unexpected foreign point rather than returning its content.
These query/filter/limit semantics use the official
[Qdrant Query API](https://api.qdrant.tech/api-reference/search/query-points) and
[filter conditions](https://qdrant.tech/documentation/search/filtering/).

**TopK.** The maximum returned chunk count, default 5 and bounded to 1..20.
It is not a file count: several overlapping chunks can come from one file.
Qdrant applies the limit to candidates satisfying the repository filter.
Results keep Qdrant's ranking and receive one-based ranks; fewer available
points produce fewer results. The prototype does not add a score threshold,
deduplicate files, rerank, or combine multiple retrieval methods.

**Score and source.** With the indexed Cosine configuration, a higher score
indicates closer vector direction. It does not certify relevance or correctness
and is not a confidence probability. The service returns the database score,
not a generated value or a recomputation over displayed text. It requests only
the required payload fields, validates a canonical UUID and matching chunk ID,
checks relative path, strict positive inclusive lines and matching content line
count, and retains complete source content with original whitespace. Unexpected
payload fields are not exposed; malformed results fail without partial source.
Each chunk is bounded to the existing 8192-character embedding guard and each
response to at most 20 chunks. No full vector is returned.

**Indexing versus retrieval.** Indexing writes chunk embeddings once; retrieval
embeds each query and reads existing points. The returned stored code can be
inspected now. Context construction and asking an LLM to generate an answer
remain Stage 3 work.

### Request, response and errors

```json
{"repository":"demo-repository","query":"退款逻辑在哪里？","top_k":5}
```

`top_k` may be omitted. Fields other than these three are rejected, including
arbitrary collection/model overrides. Input types are strict: booleans, floats
and numeric strings are not accepted for TopK. Query validation preserves the
original nonblank string, including legitimate whitespace, while logging it as
a JSON string so newlines/control characters do not create extra log lines.

Response fields are `repository`, `query`, `top_k`, `collection`,
`embedding_model`, `embedding_dimension`, `retrieval_latency_ms`, and `results`.
Each result contains `rank`, `chunk_id`, `repository`, `score`, `path`,
`language`, `start_line`, `end_line`, and `content`. Latency is measured from
the service's collection check through result projection, including embedding
and Qdrant I/O; it excludes client construction/closure and HTTP transport.
Application logs include query/repository/TopK/latency/result count/model/dimension
and collection. They do not include code, vectors, keys or authorization headers.

| Condition | Result |
| --- | --- |
| Compatible indexed collection and matching repository points | 200, at most TopK ranked full chunks |
| Compatible collection but no matching repository points, including an empty indexed repository | 200, `results: []` |
| Invalid name/query/TopK/extra fields | 422 before provider/client creation |
| Missing embedding configuration | 503 before Qdrant client creation |
| Missing collection | 404; index the repository before searching |
| Wrong vector layout/metric, missing or incompatible identity, wrong query dimension | 409; no search/write |
| Embedding failure or invalid query vector | 502; no similarity query |
| Qdrant connection/auth/query failure | Safe 503 without upstream body |
| Malformed result, foreign repository, mismatched point metadata or source range | Safe 502; no partial code response |

A syntactically valid repository name that has never been indexed is not a disk
error: the index has no matches and returns an empty list. Searching requires no
current repository directory. Metadata compatibility is checked even for an
empty collection. Neither an absent collection nor unknown metadata is silently
created or bound. Existing indexing replacement can expose an empty/partial
index to concurrent searches; Task 6 has no transaction or generation switch.
Read-only retrieval does not add that consistency guarantee.

### Actual results and verification

The real local API queried the eight existing `repopilot_code` points with
`qwen3-embedding:0.6b`, measured dimension 1024. The complete requests/responses
are saved in `stage-2-task-7-verification.json`. For the measured Top5 request
`退款逻辑在哪里？`:

| Rank | Score | Path | Inclusive lines |
| ---: | ---: | --- | --- |
| 1 | 0.48684692 | `README.md` | 1..21 |
| 2 | 0.4854089 | `frontend/src/types.ts` | 1..5 |
| 3 | 0.44492742 | `backend/service/RefundService.java` | 1..19 |
| 4 | 0.44356453 | `frontend/src/RefundPanel.vue` | 1..22 |
| 5 | 0.44177508 | `config/repository.json` | 1..5 |

The returned refund chunk was the full stored 19-line source, including the
actual call `wallets.credit(userId, amount);`, UUID
`67881397-a785-5ed6-ad27-40ce2d67e8e6`. All returned content was compared with its
source file's exact line slice. None was truncated or supplied by an LLM.

The actual TopK comparison returned 1, 3, 5 and 8 chunks for K=1,3,5,20.
README ranked first; the refund implementation entered at rank 3. Requesting
all eight also added lower-scoring controller/wallet/schema material. For
`钱包余额是如何增加的？`, `WalletService.java` ranked first with score
**0.46036983**, lines 1..23. This is a small handwritten fixture observation,
not a retrieval benchmark or proof of correctness. Each request independently
embeds its query; the recorded Top1/Top3 and Top5/Top20 requests had slightly
different scores. The table quotes the saved Top5 run. No deterministic
floating-point reproducibility promise is made.

The verification compared all persistent point IDs/payloads/vectors and
collection metadata before/after all searches. Their SHA-256 hashes matched;
all source hashes also matched. An unindexed name returned an empty result,
invalid requests returned 422, and process health returned 200. The eight
persistent demonstration points remain available.

The new offline suite passed **78 tests**; the full backend suite passed **466
tests**, with five skips: four opt-in external-service tests and the existing
Windows file-symbolic-link privilege limitation. Two existing deprecation
warnings remain. The retrieval integration was separately enabled and passed
against real local services: two repositories each indexed two actual fixture
files, searches with K=1,5,20 returned only the selected repository and at most
its two chunks, payload source slices matched, and points/metadata stayed
unchanged. Only the uniquely named test collection was cleaned up. Frontend
TypeScript/Vite build passed.

README includes API startup, indexing prerequisites, UTF-8 PowerShell requests,
Swagger browser usage and optional integration test commands. There is no new
Vue page in Task 7.

## Task 7 boundary

Task 8 adds the Retrieval Debug frontend for repository/query/TopK and ranked
score/path/language/line/content display. Task 7 stops after the backend API and
real vector search verification. Hit@K / Recall@K evaluation, context injection,
RAG answers, AST chunking, BM25, hybrid search, reranking and repository-agent
orchestration remain unimplemented. The existing Stage 1 fixed-info tool remains
available.

## Task 8: Retrieval Debug frontend

| File | Responsibility |
| --- | --- |
| `apps/web/src/App.vue` | LLM/retrieval workspace navigation and hash selection; retain mounted workspaces |
| `apps/web/src/components/ChatWorkspace.vue` | Existing four Stage 1 modes moved out of the root shell |
| `apps/web/src/components/RetrievalDebug.vue` | Repository/query/TopK form, request states, ranked source cards and code preview |
| `apps/web/src/stores/retrieval.ts` | Independent in-memory form/result/error state, duplicate guard, cancellation and timeout |
| `apps/web/src/api/retrieval.ts` | JSON POST to the existing search endpoint, safe HTTP error messages and response parsing |
| `apps/web/src/api/retrieval-contract.ts` | Input guards and runtime response/request/repository boundary validation |
| `apps/web/src/types/retrieval.ts` | Request, result and response TypeScript interfaces |
| `apps/web/src/style.css` | Workspace sizing, retrieval form, scrollable results and source previews |
| `apps/web/tests/retrieval-contract.test.mjs` / `package.json` | Seven Node contract tests using existing TypeScript, with `npm test` |
| `docs/stage-2-task-8-verification.json` / `docs/assets/stage-2-task-8-retrieval.jpg` | Real rendered results, checks and browser screenshot |

### Data flow and call chain

```text
Repository + Query + TopK -> RetrievalDebug form
-> useRetrievalStore.search: capture and validate this request
-> searchCode: POST /api/retrieval/search with AbortSignal
-> existing FastAPI RetrievalService
-> independent query EmbeddingProvider -> compatible Qdrant collection
-> query_points(repository filter, TopK) -> scored payloads
-> parseSearchResponse: match this request, validate ranks/scope/content bounds
-> store response snapshot -> render rank/score/path/language/lines/full code
```

The frontend never requests an LLM answer or chooses a collection/model. It
consumes Task 7 unchanged. Valid searches clear the prior response before
loading; invalid input keeps the old response with its original request label.
An edited form shows a parameters-changed notice, so an old result is not
presented as the answer to the new query. Duplicate searches are ignored while
pending. The fields and Search button are disabled until completion, failure
or cancellation. Cancel aborts the client's fetch; a server-side read may still
finish. The 180-second timer is also cleared on every completion path.

Repository options initially contain `demo-repository`. The user can type an
already indexed name and press Enter; valid submitted names are remembered
locally during the session. This is not an API-backed repository catalogue.
Searching an unindexed name in a compatible collection returns zero chunks;
it does not scan disk or build an index. Indexing remains the Task 6 API.

### Five concepts to understand here

**Runtime contract versus TypeScript.** `types/retrieval.ts` helps development,
but an HTTP response is unknown data at runtime. `retrieval-contract.ts` checks
the response request echo, repository of every chunk, sequential ranks,
duplicate IDs, finite score/latency, positive lines and bounded nonblank source.
It projects only displayed fields. The backend continues to own stricter UUID,
path, line-content and model identity checks. The frontend does not replace
the mandatory server-side repository filter.

**TopK versus result count.** TopK is the maximum number of code chunks. The
summary displays both `results.length` and the submitted `top_k`; an indexed
repository with fewer candidates can return fewer results. A native numeric
input directly binds the form value, with an integer 1..20 guard. An immediate
search after editing K was verified to keep the input and response consistent.
Unicode query limits count code points, matching Python's character count;
the textarea's UTF-16 limit is separate from this validation.

**Similarity score versus correctness.** `RetrievalDebug.vue` displays the
actual database score, rounding only its label to six decimals and retaining
the raw value as a tooltip. A high score means closer embedding directions
under the existing Cosine configuration. It is not a probability, accuracy
metric or guarantee that the code implements the query. The rank-1 README
and rank-3 refund implementation make this distinction visible.

**Source and provenance.** Path, language and inclusive start/end lines describe
the stored chunk. The preview retains its full whitespace and contents through
Vue text interpolation, so source such as `<template>` is displayed rather
than executed. Folding/scrolling changes visibility, not the returned source.
The existing model/dimension/latency metadata describes the completed search;
the latency excludes HTTP transport and frontend rendering. Source on disk may
have changed since indexing, so preview provenance is the index snapshot.

**Retrieval state versus chat state.** A separate Pinia store and workspace
keep the query/results away from chat messages. Both views stay mounted with
`v-show`, retaining drafts and results during navigation. `#llm` / `#retrieval`
select the workspace and support hash changes. Reloads keep the selected view
but reset in-memory state. Search results are available to inspect here;
context injection and grounded LLM generation remain Stage 3.

### Actual browser results and acceptance

The normal page at `http://localhost:5173/#retrieval` used the normal API on
port 8000 and the eight previously indexed fixture points. The final recorded
request was:

```json
{"repository":"demo-repository","query":"退款逻辑在哪里？","top_k":5}
```

The actual displayed embedding was `qwen3-embedding:0.6b`, dimension **1024**;
the final displayed server latency was **665.0 ms**, rounded to one decimal.

| Rank | Raw score (tooltip) | Path | Language | Inclusive lines |
| ---: | ---: | --- | --- | --- |
| 1 | 0.48684692 | `README.md` | markdown | 1..21 |
| 2 | 0.4854089 | `frontend/src/types.ts` | typescript | 1..5 |
| 3 | 0.44492742 | `backend/service/RefundService.java` | java | 1..19 |
| 4 | 0.44356453 | `frontend/src/RefundPanel.vue` | vue | 1..22 |
| 5 | 0.44177508 | `config/repository.json` | json | 1..5 |

The expanded refund source contains the real
`wallets.credit(userId, amount);` call. A Top1 browser request returned only
README; the Top5 request included the implementation at rank 3. A separately
entered `not-indexed` repository returned zero chunks. These are observations
on this handwritten fixture, not Hit@K/Recall@K results. An earlier browser
run had slightly different scores/latency; both runs are preserved in the
verification JSON instead of treating floating-point results as fixed.

Browser checks confirmed blank-query and invalid-TopK messages, pending
controls/duplicate guard, changed-parameter notice, clear results, custom
repository selection, empty results and workspace state retention. The migrated
Stage 1 Streaming view made a real local request to `qwen2.5:0.5b`, responding
`已连接` to `只回复：已连接`. The four original modes remain visible and the
backend regression suite covers their existing endpoints.

Cancellation and a safe 503 message/retry were verified using a separate
ephemeral delayed-error API and separate Vite process. This was a UI failure
fixture, not an actual normal-service outage. Both temporary processes were
stopped afterwards; normal API/web/Qdrant stayed running. The page's console
had no captured warnings/errors during the real checks. The client timeout
exists but was not verified by waiting three minutes. Responsive CSS is included;
no separate mobile viewport acceptance is claimed.

`npm test` passed **7** frontend contract tests, `npm run build` passed TypeScript
and Vite, and the full backend suite passed **466**, with **5** existing skips
(four opt-in external-service tests and the Windows symlink privilege case)
and **2** existing deprecation warnings. The browser source contents were
compared to exact fixture line slices; fixture file hashes and the persistent
Qdrant point/collection-metadata hash matched the Task 7 record. All **8**
demonstration points remain. No backend code, model, credentials or fixture
source changed in this task. README contains startup, prerequisites, commands
and page operations.

## Task 8 boundary

Task 8 ends with inspected retrieved code in the debug page. Task 9 will add
the small retrieval dataset and runner, with explicit expected paths and real
Hit@K / Recall@K definitions and measurements. Retrieval evaluation, RAG
answers/context injection, Tree-sitter/AST chunking, BM25/hybrid search,
reranking and repository-agent orchestration remain unimplemented.

## Task 9: small retrieval evaluation

| File | Responsibility |
| --- | --- |
| `evaluation/datasets/stage2_retrieval.json` | Frozen 12 queries, exact expected paths, literal source evidence and SHA-256 fingerprints |
| `evaluation/retrieval/evaluate.py` | Standalone standard-library HTTP runner, annotation/result validation, Hit@K / Macro Recall@K and saved reports |
| `evaluation/tests/test_retrieval_evaluation.py` | 32 offline tests for metrics, annotation grounding, failure semantics and report preservation |
| `evaluation/reports/stage2_retrieval_20261004_01.json` | Complete first actual run: 12 queries, 60 ranked results, raw scores and per-query metrics |
| `evaluation/README.md` | Commands, definitions, evaluation units, safeguards and limits |
| `docs/stage-2-task-9-verification.json` | Before/after dataset/source/index hashes and independent metric recomputation |

### Data flow and call chain

```text
load_dataset -> check 12 queries, unique expected paths and real source evidence
-> verify expected-source hashes -> for each query, search_api(TopK=5)
-> POST /api/retrieval/search -> existing EmbeddingProvider + Qdrant filter
-> validate_response: request/model/rank/scope and exact local source slices
-> metrics_for_case: first 1/3/5 chunks -> distinct relevant file intersection
-> summarize: all 12 queries -> Hit@K and Macro Recall@K
-> verify source/dataset fingerprints again -> save a new complete JSON report
```

The runner uses only Python's standard library, with no new dependencies or
backend/frontend changes. It operates through the existing HTTP endpoint,
rather than reimplementing embeddings or directly querying Qdrant. It neither
indexes source nor changes search parameters beyond the existing repository,
query and TopK request. It makes one Top5 request per case. All K measurements
share that response, preserving ranking without reranking, score thresholds or
file deduplication ahead of the cutoff.

### Five concepts to understand here

**Ground truth.** `load_dataset` requires actual files, evidence snippets inside
their inclusive line ranges, and matching source hashes. Expected paths were
manually selected from the code before evaluation. For example,
`RefundService.java` lines 12..18 validate the order reference and call
`wallets.credit`; `WalletService.java` lines 10..18 validate the amount and
merge balances. README descriptions are excluded for implementation questions,
but counted for the explicit documentation question. Syntax/hash checks prove
the evidence exists; the human annotation still determines semantic relevance.
There are ten single-file cases and two multi-file cases, covering all eight
indexed files. These labels are never generated from the returned ranking.

**Hit@K.** Per query, this is a Boolean indicating whether any expected file is
found in the first K chunks. The report averages these Booleans, so Hit@3=10/12
means ten queries had at least one expected-file match. It does not mean ten
files or ten correct answers. An empty successful response is a miss.

**Recall@K.** For each query, divide distinct expected files found by distinct
expected files labelled. The report uses the arithmetic mean over queries,
called **Macro Recall@K**. A three-file question with two files found has
Recall@K=2/3 even though its Hit@K is true. Macro averaging gives each question
equal weight; pooling matched labels across all questions would be micro recall
and is a different statistic. Duplicate expected paths and empty denominators
are rejected.

**Ranking unit and relevance unit.** K counts code chunks, whereas relevance is
labelled by complete relative file path. Take the first K chunks first, then
deduplicate paths for recall. If ranks 1 and 2 are both file A and rank 3 is B,
Top2 covers only A; deduplicating before the cutoff would incorrectly move B
into Top2. Directory/case matches are exact. This evaluates file coverage,
not whether the returned chunk contains every relevant line within that file.

**A trustworthy run.** `evaluate` keeps the collection/model/dimension consistent
and compares each returned content slice to the local source. HTTP failures,
invalid/foreign results, changed source/labels/model or stale index content
abort with exit code 1 and no completed report. A failed query is not silently
removed from the denominator. Complete reports retain all expected paths,
ranked metadata/raw scores, per-query metrics and input fingerprints; existing
reports cannot be overwritten. Unit-test stub responses are kept separate from
the actual HTTP report. Neither score nor these metrics certify answer quality.

### Actual results and failure examples

The first actual run used the normal local API, the existing eight
`repopilot_code` points, `qwen3-embedding:0.6b` and measured dimension **1024**.
All 12 queries returned five real chunks. Labels/source/model/index were not
tuned after this run. Raw per-query paths/scores are saved in the report.

| K | Queries with at least one hit | Hit@K | Macro Recall@K |
| ---: | ---: | ---: | ---: |
| 1 | 6/12 | 50.00% | 50.00% |
| 3 | 10/12 | 83.33% | 77.78% |
| 5 | 12/12 | 100.00% | 93.06% |

The query `退款服务在哪里校验订单编号，然后把退款金额转回钱包？`
expected `backend/service/RefundService.java`. README ranked first at
**0.6155841**, while the correct service ranked second at **0.6145509**,
lines **1..19**. Hit@1=false and Hit@3=true follow directly from that ranking.
The wallet-table-default question expected `database/schema.sql`; it ranked
fourth at **0.34237885**, behind `WalletService.java`, metadata and README.

Both multi-file cases demonstrate the gap between Hit and Recall:

| Case | Expected files | Matching Top5 files | Hit@5 | Recall@5 |
| --- | --- | --- | --- | --- |
| `refund-validation-files` | RefundService + WalletService | RefundService (rank 4) | true | 1/2 |
| `refund-call-chain` | OrderController + RefundService + WalletService | RefundService (rank 2), OrderController (rank 5) | true | 2/3 |

Top5 missed `WalletService.java` in both cases. All ten single-file questions
had Recall@5=1, so Macro Recall@5 is
`(10 * 1 + 1/2 + 2/3) / 12 = 67/72 = 0.9305555555555556`.
The displayed 93.06% is rounded; the JSON keeps the unrounded number.
Hit@5=100% therefore describes at least one file per question, not complete
coverage. The tiny handwritten index has only eight chunks, and Top5 returns
five of them. This is a learning dataset without a held-out split, statistical
confidence estimates or evidence of production/general retrieval quality.

### Verification and boundary

The new offline evaluation suite passed **32 tests**. Independently recomputing
each per-query metric and all summary values from the saved actual rankings
matched the report. Dataset SHA-256 stayed
`ce03abcda9f247f32122a5bb556baf81cbd4f61ff181ef6d576af5b726d6b5fc`.
All ten fixture file hashes (including the two non-indexed files), all eight
points/vectors/payloads, and collection metadata matched before/after and the
Task 8 snapshot. Verification records the combined index SHA-256, without
copying complete vectors. No fixture, backend/frontend source, `.env`, model
configuration or persisted index was changed. Only evaluation artifacts,
tests and incremental documentation were added.

Commands are in README and `evaluation/README.md`. The evaluator works without
the web app; the normal API/Qdrant/embedding service and compatible index are
required. Full backend/frontend/Stage 2 acceptance is reserved for Task 10.
No LLM answer, LLM judge, AST/Tree-sitter, BM25/hybrid retrieval, reranker or
agent loop was introduced.

## Task 10: final tests, documentation and acceptance

Final acceptance was performed on **2026-10-04, Asia/Shanghai** with Python
3.12.14, Node 24.16.0, local Ollama and Qdrant 1.19.1. The existing handwritten
fixture, frozen dataset, business backend/frontend source, private `.env` and
persistent index were preserved. The only code edit in this task corrects the
real embedding integration test's assumption about repeated model outputs.

### Actual architecture and concept map

Indexing and retrieval use separate pipelines:

```text
POST /api/indexing/repositories/{repository}
→ controlled workspaces child → Scanner → line Chunker
→ independent EmbeddingProvider.embed_texts (batches)
→ validate actual vectors and collection identity
→ replace only this repository's Qdrant points

RetrievalDebug.vue → Pinia retrieval store → frontend API/contract validation
→ POST /api/retrieval/search → query EmbeddingProvider (same identity)
→ Qdrant query_points (mandatory repository filter, limit=TopK)
→ validate point payload → ranked chunks → escaped code previews

fixed evaluation dataset → normal retrieval HTTP route (Top5 once per query)
→ first 1/3/5 chunks → unique matched expected paths
→ Hit@K / Macro Recall@K → new JSON report
```

The retrieval pipeline does not call the chat provider. The original Stage 1
chat, SSE streaming, structured analysis and fixed-info tool calling remain
available in `ChatWorkspace.vue` and their original FastAPI routes.

| Concept | Meaning in RepoPilot | Actual implementation |
| --- | --- | --- |
| Embedding | Model inference maps text to numeric coordinates; the provider has an independent interface and configuration | [`embedding/base.py`](../services/api/app/embedding/base.py), [`provider.py`](../services/api/app/embedding/provider.py), [`factory.py`](../services/api/app/embedding/factory.py) |
| Vector | The numeric list for one chunk or query; this configured model actually returns 1024 coordinates | [`embedding/validation.py`](../services/api/app/embedding/validation.py), [`api/embeddings.py`](../services/api/app/api/embeddings.py) |
| Qdrant | The persistent vector database used to store points and query vector similarity with payload filters | [`infra/docker-compose.yml`](../infra/docker-compose.yml), [`retrieval/vector_store.py`](../services/api/app/retrieval/vector_store.py) |
| Collection | One named vector space with size/distance and embedding identity; here `repopilot_code`, 1024, Cosine | [`retrieval/vector_store.py`](../services/api/app/retrieval/vector_store.py), [`embedding/identity.py`](../services/api/app/embedding/identity.py) |
| Point | One stored `id + vector + payload`; the stable UUID identifies a source chunk | [`indexing/chunker.py`](../services/api/app/indexing/chunker.py), [`indexing/models.py`](../services/api/app/indexing/models.py), [`retrieval/vector_store.py`](../services/api/app/retrieval/vector_store.py) |
| Payload | Stored source content and attributes, separate from numeric similarity coordinates | [`indexing/service.py`](../services/api/app/indexing/service.py), [`retrieval/vector_store.py`](../services/api/app/retrieval/vector_store.py) |
| Metadata | Descriptive fields: repository, path, language, inclusive lines, chunk settings, model/schema; collection identity also stores provider/model/endpoint hash | [`repository/models.py`](../services/api/app/repository/models.py), [`indexing/models.py`](../services/api/app/indexing/models.py), [`embedding/identity.py`](../services/api/app/embedding/identity.py) |
| Similarity | Cosine compares vector direction, not probability of correctness; the debug calculation rejects invalid/zero vectors | [`retrieval/similarity.py`](../services/api/app/retrieval/similarity.py), [`api/embeddings.py`](../services/api/app/api/embeddings.py) |
| TopK | Maximum ranked code chunks returned, 1..20; results may contain several chunks of the same file | [`schemas/retrieval.py`](../services/api/app/schemas/retrieval.py), [`retrieval/service.py`](../services/api/app/retrieval/service.py) |
| Indexing | Scan/chunk/embed/store so code can later be searched; repeat requests replace this repository's points | [`indexing/service.py`](../services/api/app/indexing/service.py), [`api/indexing.py`](../services/api/app/api/indexing.py) |
| Retrieval | Embed a query, search compatible stored vectors within one repository, return source chunks and scores | [`retrieval/service.py`](../services/api/app/retrieval/service.py), [`api/retrieval.py`](../services/api/app/api/retrieval.py), [`RetrievalDebug.vue`](../apps/web/src/components/RetrievalDebug.vue) |

Dimension is the length of the actual vector, not a configuration guess.
Compatible indexing/query identity is checked even when dimensions agree.
Changing `EMBEDDING_MODEL` or its endpoint requires a different collection and
a full index. An endpoint hash helps detect configuration changes; it cannot
detect an upstream replacing model weights behind an unchanged name/endpoint.

### Actual data and final measurements

The scanner returned 10 candidates, 8 supported files and 2 skips, totaling
4076 accepted bytes. Default 60-line windows with 10-line overlap produce
8 chunks and the persistent collection contains exactly **8 points**.

The recorded refund point has:

```text
id/chunk_id: 67881397-a785-5ed6-ad27-40ce2d67e8e6
repository: demo-repository
path: backend/service/RefundService.java
language: java
lines: 1..19 (inclusive)
vector dimension: 1024; collection distance: Cosine
embedding_model: qwen3-embedding:0.6b
schema_version: code-points-v1
chunk_lines: 60; overlap_lines: 10
content includes: wallets.credit(userId, amount);
vector preview: [-0.030304136, 0.071053244, -0.007012965, -0.03631072,
                  0.0369386, -0.10422923, -0.008012391, -0.023612952]
```

Only eight coordinates are published; the vector actually has 1024. The
complete payload and API samples are in the
[Task 10 acceptance record](stage-2-task-10-verification.json).
For the real browser query `退款逻辑在哪里？`, Top5 returned README, types,
RefundService, RefundPanel and repository metadata. RefundService ranked
**3**, score **0.44492742**, lines **1..19**; browser latency was **612.2 ms**.
This is a separate request from the API sample and fixed evaluation queries;
their raw scores/latencies are retained independently. A single latency is
not a performance benchmark. The [screenshot](assets/stage-2-task-10-retrieval.jpg)
shows the actual page; code text was checked against the local source.

The final fixed 12-query evaluation is saved separately as
[`stage2_retrieval_20261004_task10.json`](../evaluation/reports/stage2_retrieval_20261004_task10.json).
The original Task 9 report is unchanged. The source, query labels, model and
eight stored vectors were not tuned between runs.

| K | Queries with at least one hit | Hit@K | Macro Recall@K |
| ---: | ---: | ---: | ---: |
| 1 | 6/12 | 50.00% | 50.00% |
| 3 | 10/12 | 83.33% | 77.78% |
| 5 | 12/12 | 100.00% | 93.06% |

Hit counts queries with any expected file. Per-query Recall counts distinct
expected files retrieved divided by expected files; Macro Recall averages
queries equally. K cuts off ranked **chunks before file deduplication**.
The multi-file validation and call-chain cases still miss WalletService in
Top5, yielding recalls 1/2 and 2/3. Top5 retrieves five of only eight chunks;
100% Hit@5 does not imply complete coverage or production quality. Invalid
responses/network errors abort evaluation instead of disappearing from its
denominator. See [`evaluation/README.md`](../evaluation/README.md).

### Tests and the corrected integration assumption

| Check | Actual result |
| --- | --- |
| Default backend, `python -m uv run --locked pytest -rs` | 471 collected; 466 passed, 5 skipped; 2 warnings; 5.00 s |
| Initial explicitly enabled four integration tests | 3 passed, 1 failed; 467 deselected; 50.26 s |
| Corrected embedding integration, targeted rerun | 1 passed; 2 warnings; 2.14 s |
| Evaluation unit suite | 32 passed; 0.25 s |
| Frontend contracts | 7 passed |
| Frontend production build | TypeScript and Vite passed; 1615 modules |
| Configuration example | Active template values validated with Settings without loading private `.env` or process overrides |
| Current real browser | Loading disables form/search; five results; model/dimension/path/raw score/lines/source verified; no captured warning/error logs |

The default suite's four integration skips were separately enabled and all
four distinct tests passed across the initial run and corrected rerun. This
does not claim that a second combined four-test invocation was run. The fifth
skip is Windows file-symlink creation (`WinError 1314`); path traversal,
repository-name, junction/reparse-point and other safety tests run normally.
The two existing warnings concern Starlette's TestClient/httpx and anyio's
BlockingPortal alias. No dependency was upgraded to silence them.

The failed test assumed that identical texts always produce identical vectors
and therefore cosine exactly 1. Observed real vectors differed by up to
**0.0023201399999999983** in a coordinate. Independent cosine was
**0.9997323919289495**; the application's result was
**0.9997323919289494**, a difference of about **1.11e-16**. A vector compared
with itself returned 1.0. No cause of model variability is inferred.

[`tests/test_embedding_integration.py`](../services/api/tests/test_embedding_integration.py)
now observes the same real responses used by the API and independently checks
their cosine with **rel/abs tolerance 1e-12**. It still calls the configured
model and stubs neither vectors nor network. Existing exact self-vector unit
tests remain unchanged. The
[diagnostic](stage-2-task-10-embedding-diagnostic.json) preserves the original
observation; historical Task 3 measurements are not rewritten.

### Reproduce acceptance

Use the startup, safe first-time environment copy, locked installation,
index/query and frontend instructions in [README](../README.md).
The current environment is already running; Qdrant remains persistent and
none of these checks requires deleting the existing collection.

Offline checks:

```powershell
# Terminal 1, services/api:
python -m uv run --locked pytest -rs

# Terminal 2, apps/web:
npm.cmd test
npm.cmd run build

# Terminal 3, repository root:
& .\services\api\.venv\Scripts\python.exe -m pytest evaluation/tests -q
$env:PYTHONIOENCODING = 'utf-8'
& .\services\api\.venv\Scripts\python.exe evaluation/retrieval/evaluate.py
```

For real integrations, start the configured Qdrant and embedding provider.
In a fresh backend terminal enable all four tests explicitly:

```powershell
$env:REPOPILOT_QDRANT_INTEGRATION = '1'
$env:REPOPILOT_EMBEDDING_INTEGRATION = '1'
$env:REPOPILOT_INDEXING_INTEGRATION = '1'
$env:REPOPILOT_RETRIEVAL_INTEGRATION = '1'
try {
  python -m uv run --locked pytest -m integration -rs
} finally {
  Remove-Item Env:REPOPILOT_QDRANT_INTEGRATION
  Remove-Item Env:REPOPILOT_EMBEDDING_INTEGRATION
  Remove-Item Env:REPOPILOT_INDEXING_INTEGRATION
  Remove-Item Env:REPOPILOT_RETRIEVAL_INTEGRATION
}
```

These tests use fixed texts or temporary copies of the fixture and unique test
collections. Indexing checks 18 points → repeated 18 with the same IDs → 15
after removal → 0 for empty input. Retrieval checks two repositories containing
identical source texts with TopK 1/5/20 and verifies isolation and read-only
behavior. Their own temporary collections are cleaned in `finally` blocks.

### Final acceptance checklist

All 28 items from the requested checklist are satisfied for this prototype.
The Windows symlink skip and existing warnings above remain explicit limits.

| # | Item | Evidence |
| ---: | --- | --- |
| 1 | Stage 1 preserved | Default regression suite; four routes present; business source unchanged; Task 8 real streaming check |
| 2 | Qdrant starts | `Qdrant.ps1 status`: pinned 1.19.1 container healthy |
| 3 | FastAPI connects to Qdrant | `/health/qdrant` 200, collection exists |
| 4 | Independent Embedding Provider | `embedding/base.py`, `factory.py`, separate `EMBEDDING_*` settings |
| 5 | Actual embeddings | Real provider integration and `/api/embeddings/debug` |
| 6 | Dimension visible | Debug, collection and page show 1024 |
| 7 | Text similarity | Real related/refund vs weather requests and independently checked cosine |
| 8 | Controlled repository scan | `demo-repository`: 10 candidates, 8 accepted |
| 9 | Ignore rules | `repository/ignore.py`, skip tests and two actual fixture skips |
| 10 | Safe paths | `repository/paths.py`, scanner/retrieval tests; Windows skip disclosed |
| 11 | Simple chunking | 60/10 defaults produce eight actual chunks |
| 12 | Chunk path/language/lines | Real chunk debug response and exact source slices |
| 13 | Batch embeddings | Provider batch tests and actual indexing integration |
| 14 | Qdrant writes | Eight persistent points; unique integration collection writes |
| 15 | Payload/metadata correct | Actual refund payload, model/identity and source verification |
| 16 | Reindex does not grow duplicates | Real integration repeat: 18 → 18, same IDs |
| 17 | Query embedding | Real API evaluation and browser search |
| 18 | Vector search | Qdrant query route returns ranked source chunks |
| 19 | Repository filter | Mandatory filter, real two-repository isolation test |
| 20 | TopK | Request guard 1..20, actual integrations at 1/5/20 |
| 21 | Score/path/lines/content returned | Real API samples and current browser/source check |
| 22 | Retrieval Debug works | Current screenshot/DOM check and seven contract tests |
| 23 | Real small evaluation exists | Frozen source-grounded 12-query dataset and two measured reports |
| 24 | Actual Hit/Recall metrics | Saved final report and independent recomputation |
| 25 | pytest passes | 466 default backend + all four distinct real integrations + 32 evaluation tests |
| 26 | Frontend build passes | TypeScript and Vite build |
| 27 | README updated | Current final acceptance, startup/API/test commands and limits |
| 28 | Stage 2 docs complete | This concept/file map, checklist, real examples and 25-question study guide |

### Configuration, preservation and current boundary

`.env.example` now explains independent LLM/embedding settings, actual
dimension, collection compatibility, bounded scanning/chunking/indexing and
first-time copying. Its active values were validated against the actual
Settings model. `VITE_API_BASE_URL` is frontend-only; `QDRANT_HTTP_PORT` is
Compose-only. No secrets, private `.env` values or full vectors are published.

Before/after verification found identical fixture/dataset hashes and identical
full Qdrant vectors/payloads/collection metadata. Eight points remain; the
combined data SHA-256 is
`0049fcf6256198fd70a3b9ace450f156e5277528e2002ebc7bdd16810bfa0f4c`.
Collection names match before/after, so no test collection was left behind.
Private `.env` and business code were checked unchanged without publishing
their private configuration fingerprint. See the acceptance JSON.

Default models/database are local. A remote embedding endpoint receives query
text and indexed chunks, and Qdrant payloads contain accepted source content.
Fixed filename exclusions include `.env`, `.env.*`, `*.pem`, `*.key`, lock
files and required ignored directories. This scanner is not a secret-content
detector and does not read `.gitignore`. Use approved test repositories.

Index replacement is synchronous and not transactional. A failure after delete
can leave a partial index; retry a complete index. The lock is process-local;
use one API worker. No incremental index, automatic repository inventory,
token-aware/AST chunking, authentication or production scale evaluation is
provided. Browser state survives workspace switching, not page refresh.

**Stage 2 is complete and stops at finding code.** RAG answers/context
injection, Tree-sitter/AST, sparse/BM25/hybrid search, reranking, query expansion,
memory and repository-agent orchestration have not been added. The Stage 1
fixed-info tool-calling demonstration remains; it is not a repository agent.
The next stage requires a new explicit instruction and an agreed Stage 3 scope.

## Primary references

- [Qdrant installation](https://qdrant.tech/documentation/install/)
- [Qdrant health endpoints](https://qdrant.tech/documentation/ops-monitoring/monitoring/#kubernetes-health-endpoints)
- [Pinned Qdrant release](https://github.com/qdrant/qdrant/releases/tag/v1.19.1)
- [Pinned Docker image source](https://github.com/qdrant/qdrant/blob/v1.19.1/Dockerfile)
