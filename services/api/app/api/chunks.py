from fastapi import APIRouter, HTTPException

from app.core.exceptions import ChunkingLimitError
from app.indexing.chunker import create_code_chunker
from app.repository.scanner import create_repository_scanner
from app.schemas.chunks import (
    ChunkDebugRequest, ChunkDebugResponse, CodeChunkResponse, FileChunkSummary,
)
from app.schemas.repositories import SkippedPathInfo


router = APIRouter(prefix="/api/chunks", tags=["chunks"])


@router.post("/debug", response_model=ChunkDebugResponse)
def chunk_debug(body: ChunkDebugRequest) -> ChunkDebugResponse:
    """Count all chunks, retaining only a bounded source preview; no index writes."""
    try:
        chunker = create_code_chunker(
            chunk_lines=body.chunk_lines, overlap_lines=body.overlap_lines,
        )
    except ValueError:
        # Request overrides may conflict with the other value from environment.
        raise HTTPException(
            status_code=422,
            detail="overlap_lines must be smaller than chunk_lines; provide both options when overriding defaults.",
        ) from None

    scan = create_repository_scanner().scan(body.repository)
    preview: list[CodeChunkResponse] = []
    summaries: list[FileChunkSummary] = []
    without_chunks: list[str] = []
    total = 0
    for file in scan.files:
        file_count = 0
        for chunk in chunker.iter_file_chunks(file):
            total += 1
            file_count += 1
            if total > chunker.max_chunks:
                raise ChunkingLimitError("Chunking exceeds the chunk count limit.")
            if len(preview) < body.preview_limit:
                preview.append(CodeChunkResponse.from_chunk(chunk))
        summaries.append(FileChunkSummary(
            path=file.path, language=file.language, line_count=file.line_count,
            chunks_created=file_count,
        ))
        if file_count == 0:
            without_chunks.append(file.path)
    return ChunkDebugResponse(
        repository=scan.repository,
        chunk_lines=chunker.chunk_lines, overlap_lines=chunker.overlap_lines,
        files_scanned=scan.files_scanned, supported_files=len(scan.files),
        skipped_files=len(scan.skipped), files_chunked=len(scan.files) - len(without_chunks),
        files_without_chunks=without_chunks, chunks_created=total,
        chunks_returned=len(preview), preview_limit=body.preview_limit,
        truncated=len(preview) < total, files=summaries, chunks=preview,
        skipped=[SkippedPathInfo(path=item.path, reason=item.reason) for item in scan.skipped],
        pruned_directories=[SkippedPathInfo(path=item.path, reason=item.reason) for item in scan.pruned_directories],
    )
