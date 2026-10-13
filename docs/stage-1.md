# Stage 1 development notes

## Task 1: project initialization

The repository currently has two independently runnable applications:

```text
Browser -> Vue 3 / Vite (localhost:5173)

HTTP client -> FastAPI GET /health (localhost:8000) -> {"status":"ok"}
```

The FastAPI application enables CORS for local Vite development. The allowed
origins are read from `API_CORS_ORIGINS` as a comma-separated list and default to
`http://localhost:5173`.

## Task 2: basic LLM provider

The non-streaming chat call chain is:

```text
POST /api/chat
-> app/api/chat.py
-> get_llm_provider()
-> ProviderFactory
-> OpenAIProvider or OpenAICompatibleProvider
-> OpenAI SDK chat.completions.create(...)
-> ChatResponse
```

`app/core/config.py` reads configuration from process environment variables and
the repository-root `.env`. Secrets use Pydantic's `SecretStr`, so normal object
representations do not expose the API key.

The route depends only on the `LLMProvider` interface. It does not instantiate or
import an SDK client, which keeps the business endpoint independent from a
specific model vendor.

Task 2 sends exactly one `user` message and waits for the complete answer.

## Task 3: messages and multi-turn context

`POST /api/chat` now accepts an ordered `messages` array containing these roles:

- `system`: behavior and response guidance for the model.
- `user`: input written by the user.
- `assistant`: an earlier model response supplied as conversation history.

In Task 3 the client supplies `system` messages. They guide model behavior but
must not be treated as authentication or an authorization boundary; immutable
application policy would need to be inserted by the backend.

Example request flow:

```text
[system, user question 1]
-> model answer 1
-> caller appends assistant answer 1
-> caller appends user question 2
-> [system, user question 1, assistant answer 1, user question 2]
-> model answer 2
```

The backend does not store a session in Task 3. The second response can reference
the first exchange because the caller sends that exchange again in the next
request. `ChatRequest` validates roles, non-blank content, the list size, unknown
fields, and that the latest message is from the user. Validation preserves the
original whitespace so pasted source code is not modified.

The canonical order is an optional system message, earlier user/assistant turns
in chronological order, and the newest user message last. The API does not force
strict alternation because consecutive messages can still be valid input.

Every model has a finite context window. Task 3 does not add model-specific token
counting; if a conversation exceeds the selected model's limit, the provider
returns its normal request error and RepoPilot converts that failure to HTTP 502.
Provider configuration is resolved only after request validation, so malformed
message payloads still receive HTTP 422 even when no API key is configured.

## Task 4: streaming chat

The streaming call chain is:

```text
Vue submit
-> POST /api/chat/stream with complete messages
-> FastAPI StreamingResponse
-> LLMProvider.stream(...)
-> OpenAI SDK chat.completions.create(stream=True)
-> async generator yields provider text chunks
-> FastAPI encodes JSON SSE events
-> browser fetch ReadableStream
-> Pinia appends each delta to one assistant placeholder
-> done replaces the placeholder with the validated complete message
```

### Async generator

`LLMProvider.stream()` returns an async generator rather than one completed
string. Every `yield` makes one available text fragment move downstream while
the provider is still generating later fragments. `OpenAICompatibleProvider`
keeps the SDK stream in an async context manager, so normal completion, provider
errors, browser disconnects, and consumer cancellation release the upstream HTTP
connection.

The route accumulates the same fragments for the final assistant message. It
enforces the shared 20,000-character limit before emitting a fragment, then
validates the concatenated text with `ChatMessage` before sending `done`.

### SSE protocol

The endpoint uses a JSON-over-SSE protocol:

- `metadata`: configured model name.
- `delta`: one text fragment; whitespace and newlines are preserved.
- `done`: the complete, validated `ChatResponse` and terminal success event.
- `error`: a safe error description and terminal failure event.

The valid event sequences are `metadata -> delta* -> done` and
`metadata -> delta* -> error`. Provider configuration is resolved before the
stream response begins, so missing configuration remains an HTTP 503. Once
response headers have been sent, a midstream failure must be represented by the
`error` event because the HTTP status is already 200.

`Cache-Control: no-cache` and `X-Accel-Buffering: no` discourage HTTP proxies
from holding chunks. The response intentionally has no `Content-Length`.

### Frontend consumption and message state

The browser uses `fetch` because this is a POST request containing JSON; native
`EventSource` cannot send that body. `TextDecoder` runs in streaming mode so a
multi-byte Chinese character may safely span network chunks. A persistent text
buffer separates complete SSE frames, including CRLF frames and frames split
across arbitrary TCP chunks.

Pinia creates one empty assistant placeholder with status `streaming`. Each
`delta` appends to that same object. The `done` response is authoritative and
replaces its content, then marks it `complete`; this prevents a duplicate final
assistant message and guarantees the saved message matches the backend. If an
`error` arrives or the connection ends without `done`, existing partial text is
kept but marked `failed`, and failed assistant messages are excluded from the
next request's context.

### Streaming verification

`tests/test_streaming_transport.py` starts a real Uvicorn server on a loopback
TCP socket. Its deterministic provider yields one fragment, blocks before the
second, and is released only after an HTTP streaming client observes the first
fragment. This verifies that the ASGI/HTTP transport flushes data before model
generation finishes rather than merely checking a response that a test client
may have buffered.

With real provider credentials, the same behavior can be observed in the Vue
page or with `curl.exe -N` as documented in the README.

## Task 5: structured output

The requirement-analysis call chain is:

```text
POST /api/analyze { requirement }
-> AnalysisRequest validates caller input
-> analysis route adds a fixed system prompt
-> LLMProvider.complete_structured(..., RequirementAnalysis)
-> OpenAI-compatible JSON Schema response format
-> RequirementAnalysis.model_validate_json(...)
-> { summary, difficulty, tasks, risks }
```

Free-form natural language is useful for people but fragile for programs: field
names can change, sections can disappear, values such as difficulty can use an
unexpected vocabulary, and explanatory prose can surround the data. A planner
would otherwise need unreliable text parsing before it could make decisions.

`AnalysisRequest` validates that the incoming requirement is present, non-blank,
bounded in size, and has no unknown fields. `RequirementAnalysis` is also sent to
the provider as a compatible JSON Schema subset and validates the returned JSON
again in RepoPilot with the complete schema. The compatible subset omits grammar
keywords that Ollama does not parse, while local validation retains every
constraint. Difficulty is restricted to `easy`, `medium`, or `hard`; at least one
task is required; list sizes, item sizes, blank strings, and unknown fields are
rejected. Provider data that fails validation becomes a safe HTTP 502 response.

This gives the future Agent Planner a stable typed boundary. It can prioritize
the `tasks` array, use `difficulty` to select a strategy or model, and surface
`risks` as checks without guessing where those values appeared in prose. Task 5
only produces the plan input; Task 6 below demonstrates tool execution as a
separate flow.

## Task 6: native tool calling

Task 6 implements one complete, framework-free tool loop at `POST /api/agent`.
The tool returns the fixed `smart-community` example required by Stage 1. It does
not inspect the actual RepoPilot directory.

The exact code path is:

```text
User POST /api/agent
-> app/api/agent.py: agent_chat()
-> app/agent/tool_loop.py: ToolCallingAgent.run()
-> app/llm/compatible_provider.py: complete_with_tools()
-> LLM receives messages plus AVAILABLE_TOOLS
-> LLM returns ToolCall(name="get_repository_info", arguments={})
-> app/tools/registry.py: execute_tool_call()
-> GetRepositoryInfoArguments validates arguments
-> app/tools/repository.py: get_repository_info()
-> RepositoryInfo is serialized as the Tool Result
-> ToolCallingAgent appends assistant Tool Call and tool message to history
-> complete_with_tools() sends the expanded history back to the LLM
-> LLM returns a final answer without tool calls
-> app/api/agent.py validates it as an assistant ChatMessage
-> ChatResponse is returned to the user
```

### Responsibilities

- The Python function and its fixed result model live in
  `app/tools/repository.py`.
- The provider-facing function schema is `GET_REPOSITORY_INFO_DEFINITION` in the
  same file.
- `AVAILABLE_TOOLS` in `app/tools/registry.py` is the explicit allowlist.
- `execute_tool_call()` rejects unknown names and validates arguments before
  dispatch. It never uses shell execution, dynamic imports, or `eval`.
- `complete_with_tools()` converts the provider response into RepoPilot's typed
  `ToolCall`; provider-specific SDK objects do not leak into the agent loop.
- `ToolCallingAgent.run()` owns the loop and has a hard four-round limit.

A normal Python function call is chosen directly by application code. In tool
calling, the model only proposes a function name and JSON arguments. RepoPilot
must validate that proposal, select an allowlisted Python function, execute it,
and return the result to the model. The model needs that result in a second LLM
request because the function produced data, not the user-facing explanation;
the final response should interpret the data in the context of the user's
question.

No LangGraph is needed for this single loop. Keeping the `for` loop explicit
makes message construction, dispatch, validation, and termination visible while
the project is still demonstrating the native protocol.

## Task 7: frontend integration

The RepoPilot v0.1 page now combines the four Stage 1 interactions behind one
small mode selector:

```text
App.vue mode and form
-> Pinia chat store selects the operation
-> api/chat.ts validates the HTTP or SSE response
-> POST /api/chat | /api/chat/stream | /api/analyze | /api/agent
-> FastAPI uses the configured LLM provider
-> the store updates the conversation or structured analysis result
-> Vue renders the final state
```

Normal Chat, Streaming, and Tool Calling reuse one ordered conversation. This
keeps multi-turn context available when changing between those three transport
or execution modes. Only completed messages are included in the next request;
pending, streaming, and failed assistant placeholders are UI state rather than
model history. The current system message is prepended when the request is made.

Structured Output has separate input and result state because its response is a
typed analysis rather than a conversational message. The browser checks the
four expected fields and the difficulty enum again before displaying the result,
so an unexpected server payload becomes a visible error instead of corrupting
the page state.

Every mode uses the same busy and error boundary. Mode changes and clearing are
disabled during an active request. A normal or agent request shows a pending
assistant bubble; streaming appends text to one streaming bubble; analysis shows
a pending card. Successful responses replace those temporary states, while
failures remain visible and can be retried. The layout deliberately remains one
responsive workspace card with no additional navigation or visual system.

## Task 8: tests and final cleanup

The Stage 1 backend suite is divided by responsibility:

- `test_health.py` checks the process health endpoint.
- `test_chat.py` covers request validation, multi-turn chat, streaming event
  order, completion limits, and safe failures.
- `test_streaming_transport.py` verifies that a real Uvicorn connection flushes
  a delta before generation finishes.
- `test_analysis.py` checks the structured endpoint and its error mapping.
- `test_provider.py` checks provider request construction, structured parsing,
  tool-call parsing, streaming cleanup, factory selection, and secret redaction.
- `test_tools.py` checks the fixed tool result, provider schema, allowlist, and
  argument validation.
- `test_tool_loop.py` checks the two-step tool-result round trip, unsupported
  tool rejection, and the hard round limit.
- `test_agent.py` checks the HTTP boundary around the complete tool loop.

All provider calls in pytest are replaced by deterministic stubs or fake SDK
clients. This makes failures reproducible and prevents the test suite from using
network access, local model capacity, or paid tokens. A separate manual browser
check exercises the four UI modes against the configured local Ollama model.

The final verification commands are:

```powershell
cd services/api
uv run pytest

cd ../../apps/web
npm run build
```

The build command runs `vue-tsc` before Vite, so incompatible Vue/TypeScript
changes fail before assets are produced. Environment values are documented in
the root `.env.example`; the real `.env`, Python environments, caches,
`node_modules`, and frontend build output are excluded by `.gitignore`.

Stage 1 intentionally stops at an explicit provider abstraction and one safe,
fixed tool. It does not add repository retrieval, memory, RAG, LangGraph,
arbitrary shell execution, code modification, or multi-agent orchestration.
