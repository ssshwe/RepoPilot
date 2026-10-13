from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

from app.core.config import Settings, get_settings
from app.core.exceptions import EmbeddingConfigurationError
from app.embedding.base import EmbeddingProvider
from app.embedding.provider import OpenAICompatibleEmbeddingProvider


class EmbeddingProviderFactory:
    """Resolve only EMBEDDING_* settings; LLM settings are never a fallback."""

    @staticmethod
    def create(settings: Settings) -> EmbeddingProvider:
        if settings.embedding_provider != "openai_compatible":
            raise EmbeddingConfigurationError(
                "EMBEDDING_PROVIDER must be openai_compatible."
            )
        if not settings.embedding_model.strip():
            raise EmbeddingConfigurationError("EMBEDDING_MODEL is required.")
        if not settings.embedding_api_key.get_secret_value().strip():
            raise EmbeddingConfigurationError(
                "EMBEDDING_API_KEY is required; local Ollama accepts a placeholder."
            )

        base_url = settings.embedding_base_url.strip().rstrip("/")
        try:
            parsed = urlsplit(base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Invalid base URL")
            _ = parsed.port
        except ValueError:
            raise EmbeddingConfigurationError(
                "EMBEDDING_BASE_URL must be an HTTP(S) URL without credentials or query."
            ) from None

        return OpenAICompatibleEmbeddingProvider(
            api_key=settings.embedding_api_key.get_secret_value(),
            base_url=base_url,
            model=settings.embedding_model.strip(),
            timeout_seconds=settings.embedding_timeout_seconds,
            batch_size=settings.embedding_batch_size,
        )


@asynccontextmanager
async def get_embedding_provider() -> AsyncIterator[EmbeddingProvider]:
    """Open after request validation and close on success, failure or cancellation."""
    provider = EmbeddingProviderFactory.create(get_settings())
    try:
        yield provider
    finally:
        await provider.aclose()
