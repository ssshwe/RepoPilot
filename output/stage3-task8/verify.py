"""Verify real Citations against saved context, raw retrieval and current source files."""
from dataclasses import replace
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET

from baseline import OUT, ROOT, save, snapshot

sys.path.insert(0, str(ROOT / "services/api"))
from app.citations import CitationValidationError, CitationValidator
from app.context import BuiltContext, ContextBuilder, ContextSource
from app.main import app
from app.schemas.code_chat import CodeChatResponse
from app.schemas.retrieval import RetrievalResult

QUERY = "退款服务在哪里校验订单编号，然后把退款金额转回钱包？"
BOUNDARY = re.compile(r"\[SOURCE (S[1-9][0-9]*)\]\n(.*?)\n\[/SOURCE \1\]", re.DOTALL)


def load(path):
    return json.loads(path.read_bytes().decode("utf-8"))


def verify_response(run, name, context_number, generation_number=None):
    directory = OUT / run
    record = load(directory / f"response-{name}.json")
    assert record["http_status"] == 200 and record["request"]["question"] == QUERY
    response = CodeChatResponse.model_validate(record["response"])
    captured = load(directory / f"context-{context_number:03}.json")
    data = captured["context"]
    context = BuiltContext(**{**data, "sources": tuple(ContextSource(**source) for source in data["sources"])})
    results = [RetrievalResult.model_validate(result) for result in captured["input"]]
    original_map = {result.chunk_id: result for result in results}
    assert response.repository == context.repository == record["request"]["repository"]
    assert response.retrieval.used_sources == len(context.sources)
    assert response.retrieval.context_characters == len(context.text) == context.characters_used
    assert context.characters_used <= context.max_context_characters == response.retrieval.context_budget_characters
    for original in results:
        raw = (ROOT / "workspaces" / original.repository / original.path).read_bytes().decode("utf-8-sig")
        assert original.content == "".join(raw.splitlines(keepends=True)[original.start_line - 1:original.end_line])
    matches = list(BOUNDARY.finditer(context.text))
    assert len(matches) == len(context.sources)
    assert "\n\n".join(match.group(0) for match in matches) == context.text
    for source, match in zip(context.sources, matches, strict=True):
        body = json.loads(match.group(2))
        assert source.source_id == match.group(1)
        original = original_map[source.chunk_id]
        for key in ("repository", "path", "language", "symbol", "node_type", "parent_symbol", "chunking_strategy", "chunking_version"):
            assert getattr(source, key) == body[key] == getattr(original, key)
        raw = (ROOT / "workspaces" / source.repository / source.path).read_bytes().decode("utf-8-sig")
        assert source.content == body["content"] == "".join(raw.splitlines(keepends=True)[source.start_line - 1:source.end_line])
        assert body["lines"] == {"start": source.start_line, "end": source.end_line}
    projected = CitationValidator().validate(response.repository, response.source_ids, context=context, retrieval_results=results)
    assert [item.model_dump() for item in projected] == [item.model_dump() for item in response.citations]
    for citation in response.citations:
        source = context.source_map[citation.source_id]
        assert citation.path == source.path and citation.symbol == source.symbol
        assert citation.repository == source.repository
        assert (citation.start_line, citation.end_line) == (source.start_line, source.end_line)
    if generation_number is not None:
        generation = load(directory / f"generation-{generation_number:03}.json")
        assert len(generation["actual_generation_http_requests"]) == 1
        assert generation["actual_generation_http_requests"][0]["host"] == "127.0.0.1"
        assert generation["model"] == "qwen2.5:0.5b"
        assert generation["messages"][-1]["content"] == QUERY
        assert context.text in generation["messages"][1]["content"]
        assert set(generation["result"]) == {"answer", "source_ids"}
        assert generation["result"]["source_ids"] == response.source_ids
        assert generation["result"]["answer"] == response.answer
    else:
        assert response.generation.called is False and response.model is None
        assert response.citations == [] and response.source_ids == []
    return response, context, results


def sync_context(context, source):
    sources = (source, *context.sources[1:])
    text = "\n\n".join(ContextBuilder.format_source(item) for item in sources)
    return replace(context, sources=sources, text=text, characters_used=len(text))


def main():
    full, context, results = verify_response("full", "refund", 1, 1)
    empty, _, _ = verify_response("full", "empty", 2)
    budget, small_context, small_results = verify_response("budget", "refund", 1, 1)
    assert full.retrieval.context_characters == 4110 and len(full.citations) == 1
    assert (full.citations[0].start_line, full.citations[0].end_line) == (12, 18)
    assert budget.retrieval.context_characters == 698 and budget.retrieval.context_budget_characters == 700
    assert (budget.citations[0].start_line, budget.citations[0].end_line) == (12, 15)
    assert small_context.sources[0].original_end_line == 18 and small_context.sources[0].budget_truncated
    assert "wallets.credit" not in small_context.sources[0].content
    assert budget.answer == QUERY  # Preserve the real poor answer; do not replace it.
    assert empty.status == "insufficient_evidence"

    # Controlled pure replays of REAL saved inputs; not new LLM or HTTP outputs.
    source = context.sources[0]
    failures = []
    for name, ids, candidate, originals in (
        ("unknown_id", ["S99"], context, results),
        ("duplicate_id", ["S1", "S1"], context, results),
        ("foreign_source", ["S1"], sync_context(context, replace(source, repository="other")), results),
        ("invalid_line_metadata", ["S1"], sync_context(context, replace(source, end_line=99)), results),
        ("not_in_emitted_context", ["S2"], small_context, small_results),
        ("original_end_used_for_truncated_content", ["S1"], sync_context(small_context, replace(small_context.sources[0], end_line=18)), small_results),
        ("wrong_source_slice", ["S1"], sync_context(context, replace(source, content=source.content.replace("Order ID", "PRIVATE_CHANGED"))), results),
        ("context_map_mismatch", ["S1"], replace(context, text=context.text + "\nPRIVATE", characters_used=len(context.text) + 8), results),
    ):
        try:
            CitationValidator().validate("demo-repository", ids, context=candidate, retrieval_results=originals)
        except CitationValidationError as error:
            assert str(error) == "Invalid citation IDs or source metadata for this request."
            failures.append({"case": name, "rejected": True, "partial_citations_returned": False})
        else:
            raise AssertionError(name + " was accepted")
    assert CitationValidator().validate("demo-repository", [], context=context, retrieval_results=results) == ()
    save("controlled-replays.json", {"kind": "pure controlled mutations of saved real metadata/IDs; no inference or index writes",
                                    "failures": failures, "empty_ids_return_empty_citations": True})

    for run, expected in (("full", (2, 1, 3)), ("budget", (1, 1, 2))):
        finished = load(OUT / run / "server-finished.json")
        assert (finished["contexts_built"], finished["generation_calls"], finished["citation_validation_calls"]) == expected
        assert finished["actual_generation_http_requests"] == 1 and finished["stopped"]
    root = ET.parse(OUT / "backend-tests.xml").getroot()
    suite = root.find("testsuite")
    assert (int(suite.attrib["tests"]), int(suite.attrib["skipped"])) == (901, 6)
    assert int(suite.attrib["failures"]) == int(suite.attrib["errors"]) == 0
    citation_cases = [case for case in root.iter("testcase") if case.attrib["classname"].endswith("test_citations")]
    chat_cases = [case for case in root.iter("testcase") if case.attrib["classname"].endswith("test_code_chat")]
    assert len(citation_cases) == 103 and len(chat_cases) == 68
    evaluation = ET.parse(OUT / "evaluation-tests.xml").getroot().find("testsuite")
    assert (int(evaluation.attrib["tests"]), int(evaluation.attrib["failures"]), int(evaluation.attrib["errors"])) == (42, 0, 0)
    schema = app.openapi()["components"]["schemas"]
    assert "citations" in schema["CodeChatResponse"]["required"]
    assert set(schema["Citation"]["properties"]) == {"source_id", "repository", "path", "symbol", "start_line", "end_line"}

    after = snapshot()
    assert after == load(OUT / "qdrant-before.json")
    save("qdrant-after.json", after)
    old = load(OUT / "workspace-before.json")
    changed = sorted(name for name, digest in old.items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest)
    assert changed == ["README.md", "services/api/app/rag/service.py", "services/api/app/schemas/code_chat.py", "services/api/tests/test_code_chat.py"]
    production = next(item for item in after["collections"] if item["name"] == "repopilot_code")
    preservation = {"protected_files": len(old), "changed_existing_files": changed, "unexpected_changes": [],
                    "env_and_lockfiles_unchanged": True, "context_builder_and_prompt_unchanged": True,
                    "frontend_fixture_dataset_and_past_evidence_unchanged": True, "qdrant_exact_match": True,
                    "production_points": production["points_count"], "production_points_sha256": production["points_sha256"]}
    save("preservation.json", preservation)
    bad_case = {"kind": "real HTTP budget result", "question": QUERY, "actual_answer": budget.answer,
                "source_ids": budget.source_ids, "citations": [item.model_dump() for item in budget.citations],
                "problem": "The answer echoes the question; valid mapped Citations do not imply semantic answer correctness.",
                "context_characters": 698, "original_lines": [12, 18], "emitted_and_cited_lines": [12, 15],
                "wallet_call_not_in_context": True, "answer_was_not_rewritten": True}
    save("bad-case.json", bad_case)
    evidence = {
        "task": "Stage 3 / Task 8: Citation Validator", "status": "complete", "date": "2026-10-07", "timezone": "Asia/Shanghai",
        "tests": {"backend_collected": 901, "backend_passed": 895, "backend_skipped": 6, "backend_warnings": 2,
                  "citation_cases": 103, "code_chat_cases": 68, "new_cases": 111, "evaluation_passed": 42,
                  "frontend_passed": 9, "frontend_typecheck_and_build": "passed", "frontend_modules": 1615},
        "policy": "All-or-nothing rejection; no partial Citations or answer on invalid metadata/IDs",
        "citation_fields": ["source_id", "repository", "path", "symbol", "start_line", "end_line"],
        "citation_coordinates": "Path/Symbol from validated original backend metadata; Lines from exact emitted slice",
        "real_api_requests": 3, "real_generation_http_requests": 2,
        "llm_model": "qwen2.5:0.5b", "embedding_model": "qwen3-embedding:0.6b", "embedding_dimension": 1024,
        "real_source_slices_verified": True, "model_schema_still_only_answer_and_ids": True,
        "full_response": full.model_dump(), "budget_response": budget.model_dump(), "empty_response": empty.model_dump(),
        "controlled_real_metadata_replays": failures, "preservation": preservation, "bad_case": bad_case,
        "temporary_acceptance_apis_stopped": True, "budget_override_only_in_acceptance_process": True,
        "not_implemented": ["Task 9 dedicated injection review", "Code Chat UI", "Issue Analyzer", "Semantic answer validation"],
        "next_task": "Task 9: Prompt Injection boundary review",
    }
    (ROOT / "docs/stage-3-task-8-verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": True, "backend_passed": 895, "new_cases": 111,
                      "full_citation_lines": [12, 18], "budget_citation_lines": [12, 15],
                      "controlled_invalid_cases_rejected": len(failures), "protected_files": len(old),
                      "changed_existing_files": changed, "production_points": production["points_count"],
                      "exact_index_match": True}, indent=2))


if __name__ == "__main__":
    main()
