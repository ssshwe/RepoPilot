from collections.abc import Callable
from time import perf_counter

from pydantic import BaseModel, ValidationError

from app.citations import CitationValidationError, CitationValidator
from app.context import ContextBuilder, ContextBuilderError
from app.core.exceptions import RAGResultError
from app.llm.base import LLMProvider
from app.rag.prompt import (
    INSUFFICIENT_EVIDENCE, build_rag_messages, context_character_limit, repository_answer_model,
)
from app.rag.output_safety import validate_rag_answer_safety
from app.retrieval.service import RetrievalService
from app.schemas.code_chat import (
    CodeChatGeneration, CodeChatRequest, CodeChatResponse, CodeChatRetrieval, CodeChatSource,
)
from app.schemas.retrieval import RetrievalSearchRequest


class CodeChatService:
    def __init__(
        self, retrieval: RetrievalService, builder: ContextBuilder, resolve_llm: Callable[[], LLMProvider],
        citation_validator: CitationValidator | None = None,
    ) -> None:
        self.retrieval, self.builder, self.resolve_llm = retrieval, builder, resolve_llm
        self.citation_validator = citation_validator if citation_validator is not None else CitationValidator()

    async def answer(self, request: CodeChatRequest) -> CodeChatResponse:
        retrieval = await self.retrieval.search(RetrievalSearchRequest(
            repository=request.repository, query=request.question, top_k=request.top_k,
        ))
        if (retrieval.repository != request.repository or retrieval.query != request.question
                or retrieval.top_k != request.top_k or len(retrieval.results) > request.top_k):
            raise RAGResultError("Code chat received an inconsistent retrieval response.")
        limit = min(self.builder.max_context_characters,
                    context_character_limit(request.repository, self.builder.max_context_chunks))
        builder = self.builder if limit == self.builder.max_context_characters else ContextBuilder(
            max_context_chunks=self.builder.max_context_chunks, max_context_characters=limit,
        )
        try:
            context = builder.build(request.repository, retrieval.results)
            self.citation_validator.validate(request.repository, [], context=context, retrieval_results=retrieval.results)
            info = CodeChatRetrieval(
                top_k=request.top_k, retrieved_chunks=len(retrieval.results), used_sources=len(context.sources),
                context_characters=context.characters_used, context_budget_characters=limit,
                budget_limited=context.budget_limited, empty_reason=context.empty_reason,
                collection=retrieval.collection, embedding_model=retrieval.embedding_model,
                embedding_dimension=retrieval.embedding_dimension, latency_ms=retrieval.retrieval_latency_ms,
            )
            messages = build_rag_messages(request.question, context) if context.sources else []
        except (ContextBuilderError, ValidationError):
            raise RAGResultError("Code chat could not build valid repository context.") from None
        except CitationValidationError:
            raise RAGResultError("Code chat could not validate source metadata.") from None
        if not context.sources:
            return CodeChatResponse(
                repository=request.repository, question=request.question, answer=INSUFFICIENT_EVIDENCE,
                source_ids=[], citations=[], sources=[],
                status="insufficient_evidence", model=None, retrieval=info,
                generation=CodeChatGeneration(called=False, latency_ms=0),
            )
        response_model = repository_answer_model(context)
        provider = self.resolve_llm()  # Resolve only after retrieval/context validation.
        started = perf_counter()
        try:
            generated = await provider.complete_structured(messages, response_model)
            # Revalidate providers returning mutated/model_construct objects too.
            if not isinstance(generated, BaseModel):
                raise ValueError("Invalid structured result type")
            result = response_model.model_validate(generated.model_dump(warnings=False), strict=True)
        except Exception:
            raise RAGResultError("Code chat generation failed or returned invalid structured output.") from None
        elapsed = round((perf_counter() - started) * 1000, 2)
        try:
            citations = self.citation_validator.validate(
                request.repository, result.source_ids, context=context, retrieval_results=retrieval.results,
            )
        except CitationValidationError:
            raise RAGResultError("Code chat could not validate citations.") from None
        supported = bool(citations)
        # Unreferenced output is replaced by the fixed evidence-insufficient text.
        # Check referenced answers before any text or Citation is returned.
        if supported:
            validate_rag_answer_safety(result.answer)
        source_map = context.source_map
        previews = [CodeChatSource(source_id=citation.source_id,
                                  language=source_map[citation.source_id].language,
                                  content=source_map[citation.source_id].content) for citation in citations]
        return CodeChatResponse(
            repository=request.repository, question=request.question,
            answer=result.answer if supported else INSUFFICIENT_EVIDENCE,
            source_ids=result.source_ids, citations=list(citations), sources=previews,
            status="answered" if supported else "insufficient_evidence",
            model=provider.model_name, retrieval=info, generation=CodeChatGeneration(called=True, latency_ms=elapsed),
        )
