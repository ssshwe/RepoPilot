from pydantic import BaseModel, Field, model_validator

from app.indexing.models import CodeChunk
from app.schemas.repositories import RepositoryScanRequest, SkippedPathInfo


class ChunkDebugRequest(RepositoryScanRequest):
    chunk_lines: int | None = Field(default=None, strict=True, ge=1, le=200)
    overlap_lines: int | None = Field(default=None, strict=True, ge=0, le=199)
    preview_limit: int = Field(default=5, strict=True, ge=1, le=20)

    @model_validator(mode="after")
    def check_overlap(self) -> "ChunkDebugRequest":
        if (
            self.chunk_lines is not None and self.overlap_lines is not None
            and self.overlap_lines >= self.chunk_lines
        ):
            raise ValueError("overlap_lines must be smaller than chunk_lines.")
        return self


class CodeChunkResponse(BaseModel):
    chunk_id: str
    repository: str
    path: str
    language: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content: str = Field(min_length=1)

    @classmethod
    def from_chunk(cls, chunk: CodeChunk) -> "CodeChunkResponse":
        return cls(
            chunk_id=chunk.chunk_id, repository=chunk.repository, path=chunk.path,
            language=chunk.language, start_line=chunk.start_line,
            end_line=chunk.end_line, content=chunk.content,
        )


class FileChunkSummary(BaseModel):
    path: str
    language: str
    line_count: int = Field(ge=0)
    chunks_created: int = Field(ge=0)


class ChunkDebugResponse(BaseModel):
    repository: str
    chunk_lines: int
    overlap_lines: int
    files_scanned: int
    supported_files: int
    skipped_files: int
    files_chunked: int
    files_without_chunks: list[str]
    chunks_created: int
    chunks_returned: int
    preview_limit: int
    truncated: bool
    files: list[FileChunkSummary]
    chunks: list[CodeChunkResponse]
    skipped: list[SkippedPathInfo]
    pruned_directories: list[SkippedPathInfo]
