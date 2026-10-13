from abc import ABC, abstractmethod
from collections.abc import Sequence


class EmbeddingProvider(ABC):
    """A text-to-vector contract; no messages, text generation or tool calls."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the configured embedding model identifier."""

    async def embed_text(self, text: str) -> list[float]:
        """Embed one text using the same path as batch embedding."""
        return (await self.embed_texts([text]))[0]

    @abstractmethod
    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Return finite, nonzero vectors in input order with one common dimension."""

    async def aclose(self) -> None:
        """Release resources; in-process test providers need no cleanup."""
