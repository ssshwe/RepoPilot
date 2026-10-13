import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.agent import router as agent_router
from app.api.analysis import router as analysis_router
from app.api.chat import router as chat_router
from app.api.code_chat import router as code_chat_router
from app.api.chunks import router as chunks_router
from app.api.embeddings import router as embeddings_router
from app.api.indexing import router as indexing_router
from app.api.issues import router as issues_router
from app.api.repositories import router as repositories_router
from app.api.retrieval import router as retrieval_router
from app.api.vector_store import router as vector_store_router
from app.core.config import get_settings
from app.core.exceptions import (
    ChunkingLimitError,
    EmbeddingConfigurationError,
    EmbeddingProviderError,
    IndexingBusyError,
    IndexingLimitError,
    LLMConfigurationError,
    LLMProviderError,
    QdrantCollectionConflictError,
    QdrantCollectionNotFoundError,
    QdrantUnavailableError,
    RAGResultError,
    RepositoryNotFoundError,
    RepositoryPathError,
    RepositoryScanError,
    RepositoryScanLimitError,
    RetrievalResultError,
)
from app.schemas.health import HealthResponse

app_logger = logging.getLogger("app")
app_logger.setLevel(logging.INFO)
if not app_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    app_logger.addHandler(handler)

app = FastAPI(
    title="RepoPilot API",
    version="0.1.0",
    description="RepoPilot LLM API, local repository vector indexing and filtered code search.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(analysis_router)
app.include_router(agent_router)
app.include_router(vector_store_router)
app.include_router(embeddings_router)
app.include_router(repositories_router)
app.include_router(chunks_router)
app.include_router(indexing_router)
app.include_router(retrieval_router)
app.include_router(code_chat_router)
app.include_router(issues_router)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    if request.url.path in {"/api/code-chat", "/api/issue-analysis"}:
        # Never echo invalid question data (including unpaired surrogates) into
        # JSONResponse. Preserve all existing endpoints' validation responses.
        detail = "Invalid code chat request." if request.url.path == "/api/code-chat" else "Invalid issue analysis request."
        return JSONResponse(status_code=422, content={"detail": detail})
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(RAGResultError)
async def rag_result_handler(_request: Request, exc: RAGResultError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(RetrievalResultError)
async def retrieval_result_handler(_request: Request, exc: RetrievalResultError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(IndexingBusyError)
async def indexing_busy_handler(_request: Request, exc: IndexingBusyError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(IndexingLimitError)
async def indexing_limit_handler(_request: Request, exc: IndexingLimitError) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": str(exc)})


@app.exception_handler(ChunkingLimitError)
async def chunking_limit_error_handler(_request: Request, exc: ChunkingLimitError) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": str(exc)})


@app.exception_handler(RepositoryPathError)
async def repository_path_error_handler(_request: Request, exc: RepositoryPathError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(RepositoryNotFoundError)
async def repository_not_found_handler(_request: Request, exc: RepositoryNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(RepositoryScanError)
async def repository_scan_error_handler(_request: Request, exc: RepositoryScanError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(RepositoryScanLimitError)
async def repository_scan_limit_handler(_request: Request, exc: RepositoryScanLimitError) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": str(exc)})


@app.exception_handler(EmbeddingConfigurationError)
async def embedding_configuration_error_handler(
    _request: Request, exc: EmbeddingConfigurationError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(EmbeddingProviderError)
async def embedding_provider_error_handler(
    _request: Request, exc: EmbeddingProviderError
) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(QdrantUnavailableError)
async def qdrant_unavailable_handler(
    _request: Request, exc: QdrantUnavailableError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(QdrantCollectionNotFoundError)
async def qdrant_collection_not_found_handler(
    _request: Request, exc: QdrantCollectionNotFoundError
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(QdrantCollectionConflictError)
async def qdrant_collection_conflict_handler(
    _request: Request, exc: QdrantCollectionConflictError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(LLMConfigurationError)
async def llm_configuration_error_handler(
    _request: Request, exc: LLMConfigurationError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(LLMProviderError)
async def llm_provider_error_handler(
    _request: Request, exc: LLMProviderError
) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Return a lightweight process health signal."""
    return HealthResponse(status="ok")
