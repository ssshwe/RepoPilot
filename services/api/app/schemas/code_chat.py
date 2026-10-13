from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.citations.models import Citation, SourceId
from app.embedding.validation import MAX_EMBEDDING_TEXT_LENGTH
from app.schemas.repositories import RepositoryScanRequest


class CodeChatRequest(RepositoryScanRequest):
    question: str = Field(strict=True, min_length=1, max_length=MAX_EMBEDDING_TEXT_LENGTH)
    top_k: int = Field(default=5, strict=True, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def usable_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question must not be blank")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Question must be valid UTF-8 text") from None
        return value


class RepositoryAnswer(BaseModel):
    """The generation contract: no model-generated path/symbol/line metadata."""

    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str = Field(min_length=1, max_length=8192)
    source_ids: list[SourceId] = Field(max_length=20)

    @field_validator("answer")
    @classmethod
    def usable_answer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Answer must not be blank")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Answer must be valid UTF-8 text") from None
        return value

    @field_validator("source_ids")
    @classmethod
    def unique_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Source IDs must be unique")
        return values


class CodeChatRetrieval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k: int = Field(ge=1, le=20)
    retrieved_chunks: int = Field(ge=0, le=20)
    used_sources: int = Field(ge=0, le=20)
    context_characters: int = Field(ge=0, le=200000)
    context_budget_characters: int = Field(ge=1, le=200000)
    budget_limited: bool
    empty_reason: Literal["no_results", "budget_too_small"] | None
    collection: str
    embedding_model: str
    embedding_dimension: int = Field(ge=1, le=65536)
    latency_ms: float = Field(ge=0, allow_inf_nan=False)


class CodeChatGeneration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    called: bool
    latency_ms: float = Field(ge=0, allow_inf_nan=False)


class CodeChatSource(BaseModel):
    """Preview of an already validated cited slice, never a new file read."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_id: SourceId
    language: str = Field(min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=8192)

    @field_validator("content")
    @classmethod
    def usable_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Source content must not be blank")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Source content must be valid UTF-8") from None
        return value


class CodeChatResponse(RepositoryAnswer):
    citations: list[Citation] = Field(max_length=20)
    sources: list[CodeChatSource] = Field(max_length=20)
    question: str = Field(strict=True, min_length=1, max_length=MAX_EMBEDDING_TEXT_LENGTH)
    repository: str
    status: Literal["answered", "insufficient_evidence"]
    model: str | None
    retrieval: CodeChatRetrieval
    generation: CodeChatGeneration

    @model_validator(mode="after")
    def consistent_citations(self) -> "CodeChatResponse":
        if ([citation.source_id for citation in self.citations] != self.source_ids
                or [source.source_id for source in self.sources] != self.source_ids
                or any(citation.repository != self.repository for citation in self.citations)
                or any(len(source.content.splitlines()) != citation.end_line - citation.start_line + 1
                       for source, citation in zip(self.sources, self.citations))
                or (self.status == "answered") != bool(self.citations)):
            raise ValueError("Citations must match the response IDs, repository and status")
        return self
