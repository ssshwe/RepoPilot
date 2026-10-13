from fastapi import APIRouter, HTTPException, Path
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.exceptions import RepositoryPathError
from app.embedding.factory import EmbeddingProviderFactory
from app.indexing.ast_chunker import create_ast_code_chunker
from app.indexing.service import RepositoryIndexer, indexing_guard, prepare_repository
from app.repository.paths import validate_repository_name
from app.repository.scanner import create_repository_scanner
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from app.schemas.indexing import RepositoryIndexResponse


router = APIRouter(prefix="/api/indexing", tags=["indexing"])


@router.post("/repositories/{repository_name}", response_model=RepositoryIndexResponse)
async def index_repository(
    repository_name: str = Path(min_length=1, max_length=64),
) -> RepositoryIndexResponse:
    """Replace only this local repository's points using configured chunk settings."""
    try:
        repository = validate_repository_name(repository_name)
    except RepositoryPathError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    with indexing_guard():
        settings = get_settings()
        prepared = await run_in_threadpool(
            prepare_repository, repository, create_repository_scanner(),
            create_ast_code_chunker(), settings.indexing_max_text_characters,
        )
        provider = EmbeddingProviderFactory.create(settings)
        try:
            client = create_qdrant_client(settings)
            try:
                store = QdrantVectorStore(client, settings.qdrant_collection)
                return await RepositoryIndexer(provider, store, settings).index(prepared)
            finally:
                await client.close()
        finally:
            await provider.aclose()
