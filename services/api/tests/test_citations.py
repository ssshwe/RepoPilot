from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError
import pytest

from app.citations import Citation, CitationValidationError, CitationValidator
from app.context import ContextBuilder
from app.schemas.retrieval import RetrievalResult


def chunk(rank=1, *, path="service/refund.py", start=10, content=None, strategy="ast"):
    content = content or "def refund():\n    validate_order()\n    credit_wallet()\n"
    versions = {"ast": "ast-v1", "simple": "line-window-v1", "fallback": "ast-fallback-line-v1"}
    return RetrievalResult(
        rank=rank, chunk_id=str(uuid5(NAMESPACE_URL, f"citation-unit/{rank}/{path}/{start}")),
        repository="demo", score=1 / rank, path=path, language="python", start_line=start,
        end_line=start + len(content.splitlines()) - 1, content=content,
        symbol="refund" if strategy == "ast" else None,
        node_type="function_definition" if strategy == "ast" else None, parent_symbol=None,
        chunking_strategy=strategy, chunking_version=versions[strategy],
    )


@pytest.fixture
def case():
    results = [chunk()]
    return SimpleNamespace(results=results, context=ContextBuilder().build("demo", results), validator=CitationValidator())


def validate(case, ids=("S1",), *, context=None, results=None, repository="demo"):
    return case.validator.validate(repository, ids, context=case.context if context is None else context,
                                   retrieval_results=case.results if results is None else results)


def changed_source(context, **changes):
    sources = (replace(context.sources[0], **changes), *context.sources[1:])
    text = "\n\n".join(ContextBuilder.format_source(source) for source in sources)
    return replace(context, sources=sources, text=text, characters_used=len(text))


def test_valid_source_maps_only_authoritative_metadata_and_returns_frozen_new_records(case):
    first, second = validate(case), validate(case)
    assert first[0].model_dump() == {"source_id": "S1", "repository": "demo", "path": "service/refund.py",
                                    "symbol": "refund", "start_line": 10, "end_line": 12}
    assert first == second and first[0] is not second[0]
    with pytest.raises(ValidationError):
        first[0].path = "made-up.py"
    assert case.results[0].end_line == 12 and case.context.sources[0].end_line == 12


def test_citation_order_follows_selected_ids_without_sorting(case):
    results = [chunk(), chunk(2, path="service/wallet.py")]
    context = ContextBuilder().build("demo", results)
    citations = validate(case, ["S2", "S1"], context=context, results=results)
    assert [item.source_id for item in citations] == ["S2", "S1"]
    assert [item.path for item in citations] == ["service/wallet.py", "service/refund.py"]


@pytest.mark.parametrize("ids", [["S99"], ["S2"], ["S1", "S99"], ["S1", "S1"], ["s1"], ["S01"], ["S0"],
                               ["S21"], ["S1 "], [1], [True], [None], [{"source_id": "S1"}], "S1", None])
def test_bad_ids_fail_atomically_and_do_not_echo_ids_or_code(case, ids):
    with pytest.raises(CitationValidationError) as captured:
        validate(case, ids)
    assert str(captured.value) == "Invalid citation IDs or source metadata for this request."
    assert "credit_wallet" not in str(captured.value) and "S99" not in str(captured.value)


def test_unbounded_id_generator_is_rejected_without_consuming_it(case):
    def forbidden():
        raise AssertionError("Generator must not be consumed")
        yield "S1"
    with pytest.raises(CitationValidationError):
        validate(case, forbidden())


def test_id_list_is_bounded_before_metadata_processing(case):
    with pytest.raises(CitationValidationError):
        validate(case, ["S1"] * 21)


def test_empty_ids_are_valid_for_both_nonempty_and_empty_context(case):
    assert validate(case, []) == ()
    context = ContextBuilder().build("demo", [])
    assert validate(case, [], context=context, results=[]) == ()
    tiny = ContextBuilder(max_context_characters=1).build("demo", case.results)
    assert validate(case, [], context=tiny) == ()


def test_source_present_in_retrieval_but_omitted_from_context_cannot_be_cited(case):
    results = [chunk(), chunk(2, path="second.py")]
    context = ContextBuilder(max_context_chunks=1).build("demo", results)
    with pytest.raises(CitationValidationError):
        validate(case, ["S2"], context=context, results=results)


def test_truncated_range_is_actual_emitted_slice_not_original_end(case):
    original = case.context.sources[0]
    prefix = replace(original, content=original.content.splitlines(keepends=True)[0], end_line=10, budget_truncated=True)
    budget = len(ContextBuilder.format_source(prefix))
    context = ContextBuilder(max_context_characters=budget).build("demo", case.results)
    citation = validate(case, context=context)[0]
    assert citation.start_line == citation.end_line == 10
    assert context.sources[0].original_end_line == 12 and context.sources[0].budget_truncated


def test_overlap_and_disjoint_fragments_keep_their_real_contiguous_ranges(case):
    content = "line1\nline2\nline3\nline4\nline5\n"
    high = chunk(start=3, content="line3\n", strategy="simple")
    lower = chunk(2, start=1, content=content, strategy="simple")
    results = [high, lower]
    context = ContextBuilder().build("demo", results)
    citations = validate(case, ["S1", "S2", "S3"], context=context, results=results)
    assert [(c.start_line, c.end_line) for c in citations] == [(3, 3), (1, 2), (4, 5)]
    assert all(c.symbol is None for c in citations)


@pytest.mark.parametrize("strategy", ["simple", "fallback"])
def test_text_citations_keep_null_symbol(case, strategy):
    results = [chunk(strategy=strategy)]
    context = ContextBuilder().build("demo", results)
    assert validate(case, context=context, results=results)[0].symbol is None


def test_duplicate_chunk_input_removed_by_builder_still_has_one_citation(case):
    results = [case.results[0], case.results[0].model_copy(update={"rank": 2, "score": .4})]
    context = ContextBuilder().build("demo", results)
    assert context.duplicate_chunks == 1
    assert len(validate(case, context=context, results=results)) == 1


@pytest.mark.parametrize("changes", [
    {"start_line": 0}, {"end_line": 9}, {"start_line": True}, {"end_line": "12"},
    {"start_line": 11}, {"end_line": 13}, {"original_start_line": 9}, {"original_end_line": 13},
    {"original_start_line": False}, {"original_end_line": "12"},
    {"content": "PRIVATE_WRONG_CODE\n"}, {"content": 3},
    {"path": "../PRIVATE.py"}, {"path": "C:/PRIVATE.py"}, {"path": "other.py"},
    {"symbol": "invented"}, {"symbol": "\nPRIVATE"}, {"node_type": "class_definition"},
    {"parent_symbol": "Other"}, {"language": "java"},
    {"chunking_strategy": "fallback"}, {"chunking_version": "invented-v1"},
    {"repository": "other"}, {"chunk_id": str(uuid5(NAMESPACE_URL, "not-current-request"))},
    {"retrieval_rank": 2}, {"score": .123}, {"score": float("nan")},
    {"source_id": "S2"}, {"source_id": "S99"},
    {"overlap_trimmed": True}, {"budget_truncated": True},
    {"overlap_trimmed": 1}, {"budget_truncated": 1},
])
def test_corrupted_source_metadata_is_rejected_even_with_matching_formatted_text(case, changes):
    context = changed_source(case.context, **changes)
    with pytest.raises(CitationValidationError) as captured:
        validate(case, context=context)
    assert "PRIVATE" not in str(captured.value) and "invented" not in str(captured.value)


@pytest.mark.parametrize("changes", [
    {"repository": "other"}, {"sources": []}, {"sources": ("PRIVATE",)},
    {"text": "PRIVATE_FORGED_CONTEXT"}, {"characters_used": 0}, {"characters_used": False},
    {"retrieved_chunks": 0}, {"retrieved_chunks": True},
    {"max_context_chunks": 0}, {"max_context_chunks": True},
    {"max_context_characters": 1}, {"max_context_characters": 200001},
])
def test_context_contract_is_validated_without_echoing_it(case, changes):
    with pytest.raises(CitationValidationError):
        validate(case, context=replace(case.context, **changes))


def test_duplicate_source_map_keys_cannot_silently_overwrite_metadata(case):
    context = replace(case.context, sources=(case.context.sources[0], case.context.sources[0]))
    with pytest.raises(CitationValidationError):
        validate(case, context=context)


@pytest.mark.parametrize("repository", ["other", "Demo", "../demo", "CON", "", 3])
def test_repository_scope_is_exact_and_portable(case, repository):
    with pytest.raises(CitationValidationError):
        validate(case, repository=repository)


def test_empty_ids_do_not_bypass_foreign_metadata_outside_budget(case):
    results = [case.results[0], chunk(2, path="second.py")]
    context = ContextBuilder(max_context_characters=1).build("demo", results)
    results[-1].repository = "other"
    with pytest.raises(CitationValidationError):
        validate(case, [], context=context, results=results)


@pytest.mark.parametrize("damage", ["mutated_range", "constructed", "foreign", "rank_order", "conflicting_id", "conflicting_lines"])
def test_original_retrieval_batch_is_revalidated_before_citations(case, damage):
    results = [case.results[0].model_copy(), chunk(2, path="second.py")]
    context = ContextBuilder().build("demo", results)
    if damage == "mutated_range":
        results[0].end_line = 20
    elif damage == "constructed":
        results[0] = RetrievalResult.model_construct(rank=1, content="PRIVATE_CODE")
    elif damage == "foreign":
        results[1].repository = "other"
    elif damage == "rank_order":
        results.reverse()
    elif damage == "conflicting_id":
        results[1].chunk_id = results[0].chunk_id
    else:
        results[1].path = results[0].path
        results[1].content = "PRIVATE_LINE\nPRIVATE_LINE\nPRIVATE_LINE\n"
    with pytest.raises(CitationValidationError):
        validate(case, context=context, results=results)


def test_context_from_a_previous_retrieval_cannot_supply_new_request_citations(case):
    new_results = [chunk(path="another-request.py")]
    with pytest.raises(CitationValidationError):
        validate(case, results=new_results)


@pytest.mark.parametrize("results", [None, "PRIVATE", ["PRIVATE"], tuple("x" * 21)])
def test_non_result_inputs_fail_safely(case, results):
    with pytest.raises(CitationValidationError):
        case.validator.validate("demo", ["S1"], context=case.context, retrieval_results=results)


def test_unbounded_retrieval_generator_is_not_consumed(case):
    def forbidden():
        raise AssertionError("No generator consumption")
        yield chunk()
    with pytest.raises(CitationValidationError):
        validate(case, results=forbidden())


@pytest.mark.parametrize("changes", [
    {"path": "../wrong.py"}, {"symbol": "\nwrong"}, {"start_line": 0}, {"end_line": 9},
    {"start_line": True}, {"end_line": "12"}, {"source_id": "S99"}, {"repository": "../demo"},
    {"symbol": ""}, {"path": "service//wrong.py"}, {"unexpected": "x"},
])
def test_public_citation_schema_has_strict_legal_metadata(case, changes):
    data = validate(case)[0].model_dump()
    with pytest.raises(ValidationError):
        Citation.model_validate({**data, **changes})


def test_validator_has_no_io_or_shared_result_state(case):
    with patch("app.repository.scanner.RepositoryScanner.scan", side_effect=AssertionError("no scan")), patch(
        "app.embedding.factory.EmbeddingProviderFactory.create", side_effect=AssertionError("no embedding")), patch(
        "app.llm.factory.ProviderFactory.create", side_effect=AssertionError("no LLM")), patch(
        "app.retrieval.vector_store.create_qdrant_client", side_effect=AssertionError("no database")):
        assert validate(case)[0].source_id == "S1"
        assert validate(case, []) == ()
        assert validate(case)[0].source_id == "S1"
