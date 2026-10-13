"""Preview scanner-approved AST/fallback chunks offline; never embed or index."""

import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
import logging
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.exceptions import (
    ChunkingLimitError, RepositoryNotFoundError, RepositoryPathError, RepositoryScanError,
    RepositoryScanLimitError,
)
from app.indexing.ast_chunker import ASTCodeChunker
from app.parsing.base import CodeParsingError
from app.repository.scanner import create_repository_scanner


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repository", default="demo-repository")
    cli.add_argument("--path", help="Only preview this scanner-approved relative path.")
    cli.add_argument("--preview-limit", type=int, default=20)
    cli.add_argument("--chunk-lines", type=int, default=60)
    cli.add_argument("--overlap-lines", type=int, default=10, help="Fallback only.")
    cli.add_argument("--max-characters", type=int, default=8192)
    args = cli.parse_args()
    if not 1 <= args.preview_limit <= 100:
        cli.error("preview-limit must be between 1 and 100.")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        chunker = ASTCodeChunker(
            chunk_lines=args.chunk_lines, overlap_lines=args.overlap_lines,
            max_chunk_characters=args.max_characters,
        )
        scan = create_repository_scanner().scan(args.repository)
    except (ValueError, RepositoryNotFoundError, RepositoryPathError,
            RepositoryScanError, RepositoryScanLimitError) as exc:
        print(f"Chunk spike failed: {exc}", file=sys.stderr)
        return 1
    files = [file for file in scan.files if args.path is None or file.path == args.path]
    if args.path is not None and not files:
        print("The selected source was not found among scanner-approved files.", file=sys.stderr)
        return 1
    counts = dict(ast_parsed_files=0, fallback_files=0, failed_files=0, chunks_created=0)
    reports = []
    preview_remaining = args.preview_limit
    for file in files:
        report = dict(path=file.path, language=file.language, decoded_source_sha256=
                      hashlib.sha256(file.content.encode("utf-8")).hexdigest())
        try:
            result = chunker.chunk_file_result(file)
        except (ChunkingLimitError, CodeParsingError):
            counts["failed_files"] += 1
            reports.append(report | {"status": "failed", "chunks_created": 0})
            continue
        counts["ast_parsed_files" if result.strategy == "ast" else "fallback_files"] += 1
        counts["chunks_created"] += len(result.chunks)
        preview = result.chunks[:preview_remaining]
        preview_remaining -= len(preview)
        reports.append(report | {
            "status": "ok", "strategy": result.strategy, "fallback_reason": result.fallback_reason,
            "chunks_created": len(result.chunks), "truncated": len(preview) < len(result.chunks),
            "chunks": [asdict(chunk) for chunk in preview],
        })
    logger = logging.getLogger(__name__)
    logger.info("chunk_summary ast_parsed_files=%d fallback_files=%d failed_files=%d chunks=%d",
                counts["ast_parsed_files"], counts["fallback_files"], counts["failed_files"], counts["chunks_created"])
    print(json.dumps({
        "repository": scan.repository, "supported_files": len(files),
        "chunk_lines": args.chunk_lines, "fallback_overlap_lines": args.overlap_lines,
        "max_chunk_characters": args.max_characters,
        "packages": {name: version(name) for name in (
            "tree-sitter", "tree-sitter-java", "tree-sitter-python",
            "tree-sitter-javascript", "tree-sitter-typescript",
        )}, **counts, "files": reports,
    }, ensure_ascii=False, indent=2))
    return 1 if counts["failed_files"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
