"""Retrieve real indexed code, build bounded context, and stop before generation."""
import argparse
import asyncio
from dataclasses import asdict
import json
import logging
from pathlib import Path
import sys

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.context import ContextBuilder, ContextBuilderError, create_context_builder
from app.core.config import get_settings
from app.embedding.factory import EmbeddingProviderFactory
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from app.schemas.retrieval import RetrievalSearchRequest


async def retrieve_and_build(request: RetrievalSearchRequest, builder: ContextBuilder) -> dict:
    settings = get_settings()
    provider = EmbeddingProviderFactory.create(settings)
    try:
        client = create_qdrant_client(settings)
        try:
            retrieval = await RetrievalService(
                provider, QdrantVectorStore(client, settings.qdrant_collection), settings,
            ).search(request)
            context = builder.build(request.repository, retrieval.results)
            return {
                "request": request.model_dump(),
                "retrieval": {"collection": retrieval.collection, "embedding_model": retrieval.embedding_model,
                              "embedding_dimension": retrieval.embedding_dimension,
                              "latency_ms": retrieval.retrieval_latency_ms,
                              "results": [item.model_dump() for item in retrieval.results]},
                "context": asdict(context), "llm_called": False,
            }
        finally:
            await client.close()
    finally:
        await provider.aclose()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="demo-repository")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-chunks", type=int)
    parser.add_argument("--max-characters", type=int)
    parser.add_argument("--output", type=Path, help="Write UTF-8 JSON directly, avoiding shell pipe transcoding.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        request = RetrievalSearchRequest(repository=args.repository, query=args.query, top_k=args.top_k)
        builder = create_context_builder(max_context_chunks=args.max_chunks,
                                         max_context_characters=args.max_characters)
        result = asyncio.run(retrieve_and_build(request, builder))
        serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output is not None:
            args.output.write_text(serialized, encoding="utf-8")
    except (ValidationError, ValueError) as error:
        message = str(error) if isinstance(error, ContextBuilderError) else "Invalid request or context budget."
        print(f"Context spike failed: {message}", file=sys.stderr)
        return 1
    except Exception as error:
        print(f"Context spike failed: {type(error).__name__}; no context emitted.", file=sys.stderr)
        return 1
    if args.output is None:
        print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
