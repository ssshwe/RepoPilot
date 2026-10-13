class LLMConfigurationError(Exception):
    """Raised when required LLM settings are missing or unsupported."""


class LLMProviderError(Exception):
    """Raised when an LLM provider cannot produce a usable response."""


class EmbeddingConfigurationError(Exception):
    """Raised when the independent embedding settings are missing or invalid."""


class EmbeddingProviderError(Exception):
    """Raised when an embedding service fails or returns invalid vectors."""


class RepositoryPathError(Exception):
    """Raised when a repository name or path is outside the allowed boundary."""


class RepositoryNotFoundError(Exception):
    """Raised when a named local repository directory does not exist."""


class RepositoryScanError(Exception):
    """Raised when the repository root cannot be accessed."""


class RepositoryScanLimitError(Exception):
    """Raised when a scan would exceed its entry or total text memory budget."""


class ChunkingLimitError(Exception):
    """Raised when source text or chunk output would exceed chunking limits."""


class IndexingLimitError(Exception):
    """Raised when prepared indexing data would exceed the memory budget."""


class IndexingBusyError(Exception):
    """Raised when another indexing request owns the process-local write guard."""


class RetrievalResultError(Exception):
    """Raised when returned points cannot safely represent indexed code chunks."""


class RAGResultError(Exception):
    """Raised when repository context or structured RAG output is invalid."""


class QdrantUnavailableError(Exception):
    """Raised when Qdrant cannot be reached or rejects the configured credentials."""


class QdrantCollectionNotFoundError(Exception):
    """Raised when the configured collection has not been created."""


class QdrantCollectionConflictError(Exception):
    """Raised when a collection has an incompatible vector configuration."""

