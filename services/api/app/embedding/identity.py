import hashlib

from app.core.config import Settings


def embedding_identity(settings: Settings, model: str) -> dict[str, str]:
    """Shared indexing/query identity; never persist credentials or endpoint URL."""
    endpoint = settings.embedding_base_url.strip().rstrip("/")
    return {
        "schema_version": "code-points-v1",
        "embedding_provider": settings.embedding_provider,
        "embedding_model": model,
        "endpoint_sha256": hashlib.sha256(endpoint.encode("utf-8")).hexdigest(),
    }
