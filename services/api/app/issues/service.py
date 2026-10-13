from collections.abc import Callable
from time import perf_counter

from pydantic import BaseModel, ValidationError

from app.citations import CitationValidationError, CitationValidator
from app.context import ContextBuilder, ContextBuilderError
from app.core.exceptions import RAGResultError
from app.issues.prompt import build_issue_messages, issue_result_model
from app.llm.base import LLMProvider
from app.rag.output_safety import validate_rag_answer_safety
from app.rag.prompt import INSUFFICIENT_EVIDENCE, context_character_limit
from app.retrieval.service import RetrievalService
from app.schemas.code_chat import CodeChatGeneration, CodeChatRetrieval, CodeChatSource
from app.schemas.issue_analysis import IssueAnalysisRequest, IssueAnalysisResponse
from app.schemas.retrieval import RetrievalSearchRequest


class IssueAnalysisService:
    def __init__(self, retrieval: RetrievalService, builder: ContextBuilder,
                 resolve_llm: Callable[[], LLMProvider], citation_validator: CitationValidator | None = None):
        self.retrieval, self.builder, self.resolve_llm = retrieval, builder, resolve_llm
        self.validator = citation_validator if citation_validator is not None else CitationValidator()

    async def analyze(self, request: IssueAnalysisRequest) -> IssueAnalysisResponse:
        retrieval = await self.retrieval.search(RetrievalSearchRequest(
            repository=request.repository, query=request.issue, top_k=request.top_k))
        if (retrieval.repository != request.repository or retrieval.query != request.issue
                or retrieval.top_k != request.top_k or len(retrieval.results) > request.top_k):
            raise RAGResultError("Issue analysis received an inconsistent retrieval response.")
        limit = min(self.builder.max_context_characters,
                    context_character_limit(request.repository, self.builder.max_context_chunks))
        builder = self.builder if limit == self.builder.max_context_characters else ContextBuilder(
            max_context_chunks=self.builder.max_context_chunks, max_context_characters=limit)
        try:
            context = builder.build(request.repository, retrieval.results)
            self.validator.validate(request.repository, [], context=context, retrieval_results=retrieval.results)
            info = CodeChatRetrieval(top_k=request.top_k, retrieved_chunks=len(retrieval.results),
                used_sources=len(context.sources), context_characters=context.characters_used,
                context_budget_characters=limit, budget_limited=context.budget_limited, empty_reason=context.empty_reason,
                collection=retrieval.collection, embedding_model=retrieval.embedding_model,
                embedding_dimension=retrieval.embedding_dimension, latency_ms=retrieval.retrieval_latency_ms)
            messages = build_issue_messages(request.issue, context) if context.sources else []
        except (ContextBuilderError, ValidationError):
            raise RAGResultError("Issue analysis could not build valid repository context.") from None
        except CitationValidationError:
            raise RAGResultError("Issue analysis could not validate source metadata.") from None

        def insufficient(model=None, elapsed=0, called=False):
            return IssueAnalysisResponse(repository=request.repository, issue=request.issue,
                summary=INSUFFICIENT_EVIDENCE, related_code=[], possible_call_chain=[], analysis=[],
                modification_suggestions=[], test_suggestions=[], risks=[], source_ids=[], sources=[],
                status="insufficient_evidence", model=model, retrieval=info,
                generation=CodeChatGeneration(called=called, latency_ms=elapsed))

        if not context.sources:
            return insufficient()
        response_model = issue_result_model(context)
        provider = self.resolve_llm()
        started = perf_counter()
        try:
            generated = await provider.complete_structured(messages, response_model)
            if not isinstance(generated, BaseModel):
                raise ValueError("Invalid structured result type")
            result = response_model.model_validate(generated.model_dump(warnings=False), strict=True)
        except Exception:
            raise RAGResultError("Issue analysis generation failed or returned invalid structured output.") from None
        elapsed = round((perf_counter() - started) * 1000, 2)
        try:
            citations = self.validator.validate(request.repository, result.source_ids,
                                                context=context, retrieval_results=retrieval.results)
        except CitationValidationError:
            raise RAGResultError("Issue analysis could not validate citations.") from None
        if not citations:
            return insufficient(provider.model_name, elapsed, True)
        # Check every generated text field; IDs and coordinates are backend constrained.
        try:
            for text in [result.summary, *result.analysis, *result.modification_suggestions,
                         *result.test_suggestions, *result.risks]:
                validate_rag_answer_safety(text)
        except RAGResultError:
            raise RAGResultError("Issue analysis generation returned potentially sensitive output.") from None
        by_id = {c.source_id: c for c in citations}
        sources = [CodeChatSource(source_id=c.source_id, language=context.source_map[c.source_id].language,
                                  content=context.source_map[c.source_id].content) for c in citations]
        return IssueAnalysisResponse(repository=request.repository, issue=request.issue, summary=result.summary,
            related_code=list(citations), possible_call_chain=[by_id[s] for s in result.possible_call_chain],
            analysis=list(result.analysis), modification_suggestions=list(result.modification_suggestions),
            test_suggestions=list(result.test_suggestions), risks=list(result.risks), source_ids=result.source_ids,
            sources=sources, status="analyzed", model=provider.model_name, retrieval=info,
            generation=CodeChatGeneration(called=True, latency_ms=elapsed))
