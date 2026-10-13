from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.embedding.validation import MAX_EMBEDDING_TEXT_LENGTH
from app.schemas.embeddings import EmbeddingText
from app.schemas.repositories import RepositoryScanRequest


class RetrievalSearchRequest(RepositoryScanRequest):
    query: EmbeddingText
    top_k: int = Field(default=5, strict=True, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Query must not be blank")
        return value


class RetrievalResult(BaseModel):
    rank: int = Field(ge=1, le=20)
    chunk_id: str = Field(strict=True, min_length=1, max_length=36)
    repository: str = Field(strict=True, min_length=1, max_length=64)
    score: float = Field(allow_inf_nan=False)
    path: str = Field(strict=True, min_length=1, max_length=4096)
    language: str = Field(strict=True, min_length=1, max_length=32)
    start_line: int = Field(strict=True, ge=1)
    end_line: int = Field(strict=True, ge=1)
    content: str = Field(strict=True, min_length=1, max_length=MAX_EMBEDDING_TEXT_LENGTH)
    symbol: str | None = Field(default=None, strict=True, min_length=1, max_length=4096)
    node_type: str | None = Field(default=None, strict=True, pattern=r"^[a-z][a-z0-9_]{0,127}$")
    parent_symbol: str | None = Field(default=None, strict=True, min_length=1, max_length=4096)
    chunking_strategy: Literal["simple", "ast", "fallback"] = "simple"
    chunking_version: str = Field(default="line-window-v1", strict=True, min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def complete_chunking_metadata(cls, value: object) -> object:
        # Stage 2 points have none of these keys. Only that complete legacy
        # shape may use Simple defaults; damaged Stage 3 metadata must fail.
        fields = {"symbol", "node_type", "parent_symbol", "chunking_strategy", "chunking_version"}
        if isinstance(value, dict) and fields.intersection(value) and not fields.issubset(value):
            raise ValueError("Chunking metadata must be complete")
        return value

    @field_validator("symbol", "parent_symbol")
    @classmethod
    def valid_symbol(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)):
            raise ValueError("Symbol must be a nonblank name without control characters")
        return value

    @field_validator("path")
    @classmethod
    def relative_source_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute() or "\\" in value or ":" in value or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("Code path must be relative to the repository")
        return value

    @model_validator(mode="after")
    def valid_source_range(self) -> "RetrievalResult":
        if (
            self.end_line < self.start_line or not self.content.strip()
            or len(self.content.splitlines()) != self.end_line - self.start_line + 1
        ):
            raise ValueError("Code content must match its inclusive line range")
        versions = {"simple": "line-window-v1", "ast": "ast-v1", "fallback": "ast-fallback-line-v1"}
        if self.chunking_version != versions[self.chunking_strategy]:
            raise ValueError("Chunking strategy and version must agree")
        if self.chunking_strategy == "ast":
            if self.node_type is None:
                raise ValueError("AST chunks need their syntax node type")
            if self.parent_symbol is not None and (
                self.symbol is None or not self.symbol.startswith(self.parent_symbol + ".")
            ):
                raise ValueError("Parent symbol must own the qualified symbol")
        elif any(value is not None for value in (self.symbol, self.node_type, self.parent_symbol)):
            raise ValueError("Text chunks cannot claim AST symbols")
        return self


class RetrievalSearchResponse(BaseModel):
    repository: str
    query: str
    top_k: int
    collection: str
    embedding_model: str
    embedding_dimension: int
    retrieval_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    results: list[RetrievalResult] = Field(max_length=20)
