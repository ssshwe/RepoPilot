from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.repositories import SkippedPathInfo


class RepositoryIndexResponse(BaseModel):
    repository: str
    files_scanned: int = Field(ge=0)
    supported_files: int = Field(ge=0)
    skipped_files: int = Field(ge=0)
    chunks_created: int = Field(ge=0)
    vectors_indexed: int = Field(ge=0)
    previous_vectors: int = Field(ge=0)
    collection: str
    embedding_model: str
    embedding_dimension: int | None = Field(default=None, ge=1)
    chunk_lines: int
    overlap_lines: int
    ast_parsed_files: int = Field(default=0, ge=0)
    fallback_files: int = Field(default=0, ge=0)
    failed_files: int = Field(default=0, ge=0)
    chunking_version: str = "line-window-v1"
    fallback_chunking_version: str | None = None
    max_chunk_characters: int = Field(default=8192, ge=1)
    strategy: Literal["replace_repository"] = "replace_repository"
    skipped: list[SkippedPathInfo]
    pruned_directories: list[SkippedPathInfo]
