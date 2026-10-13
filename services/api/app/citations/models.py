from typing import Annotated

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.schemas.repositories import RepositoryScanRequest
from app.schemas.retrieval import RetrievalResult


SourceId = Annotated[str, Field(strict=True, pattern=r"^S(?:[1-9]|1[0-9]|20)$")]


class Citation(RepositoryScanRequest):
    """Only authoritative backend metadata, never model-provided coordinates."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_id: SourceId
    path: str = Field(min_length=1, max_length=4096)
    symbol: str | None = Field(min_length=1, max_length=4096)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return RetrievalResult.relative_source_path(value)

    @field_validator("symbol")
    @classmethod
    def valid_symbol(cls, value: str | None) -> str | None:
        return RetrievalResult.valid_symbol(value)

    @model_validator(mode="after")
    def ordered_lines(self) -> "Citation":
        if self.end_line < self.start_line:
            raise ValueError("Citation must have an inclusive ordered line range")
        return self
