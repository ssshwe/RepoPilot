import logging
from collections.abc import Sequence
from time import perf_counter
from urllib.parse import urlsplit

from openai import APIError, AsyncOpenAI, DefaultAsyncHttpxClient

from app.core.exceptions import EmbeddingProviderError
from app.embedding.base import EmbeddingProvider
from app.embedding.validation import (
    MAX_EMBEDDING_TEXT_LENGTH,
    validate_embedding_batch,
)


logger = logging.getLogger(__name__)
_INVALID_RESPONSE = "The embedding provider returned invalid vectors."


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Use the embeddings API with a configured local or remote compatible service."""

    def __init__(
        self, *, api_key: str, base_url: str, model: str,
        timeout_seconds: float, batch_size: int,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._batch_size = batch_size
        if client is not None:
            self._client = client
        else:
            options: dict[str, object] = {}
            if urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"}:
                options["http_client"] = DefaultAsyncHttpxClient(trust_env=False)
            self._client = AsyncOpenAI(
                api_key=api_key, base_url=base_url, timeout=timeout_seconds,
                max_retries=0, **options,
            )

    @property
    def model_name(self) -> str:
        return self._model

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if isinstance(texts, (str, bytes)):
            raise ValueError("embed_texts expects a sequence of text strings.")
        inputs = list(texts)
        if any(
            not isinstance(text, str) or not text.strip()
            or len(text) > MAX_EMBEDDING_TEXT_LENGTH
            for text in inputs
        ):
            raise ValueError("Embedding input must be nonblank and within the text limit.")
        if not inputs:
            return []

        started = perf_counter()
        vectors: list[list[float]] = []
        for offset in range(0, len(inputs), self._batch_size):
            batch = inputs[offset:offset + self._batch_size]
            try:
                response = await self._client.embeddings.create(
                    model=self.model_name, input=batch, encoding_format="float",
                )
            except APIError as exc:
                # Never pass upstream text, request bodies or headers to the API.
                raise EmbeddingProviderError(
                    "The configured embedding provider request failed."
                ) from exc

            try:
                ordered: list[Sequence[float] | None] = [None] * len(batch)
                if len(response.data) != len(batch):
                    raise ValueError("Wrong embedding count")
                for item in response.data:
                    index = item.index
                    if (
                        isinstance(index, bool) or not isinstance(index, int)
                        or not 0 <= index < len(batch) or ordered[index] is not None
                    ):
                        raise ValueError("Invalid or repeated embedding index")
                    ordered[index] = item.embedding
                if any(vector is None for vector in ordered):
                    raise ValueError("Missing embedding index")
                vectors.extend(validate_embedding_batch(ordered, len(batch)))
            except (AttributeError, TypeError, ValueError, OverflowError) as exc:
                raise EmbeddingProviderError(_INVALID_RESPONSE) from exc

        try:
            vectors = validate_embedding_batch(vectors, len(inputs))
        except (TypeError, ValueError, OverflowError) as exc:
            raise EmbeddingProviderError(_INVALID_RESPONSE) from exc
        logger.info(
            "Embedding model=%s dimension=%s texts=%s batch_size=%s latency_ms=%.2f",
            self.model_name, len(vectors[0]), len(inputs), self._batch_size,
            (perf_counter() - started) * 1000,
        )
        return vectors

    async def aclose(self) -> None:
        await self._client.close()
