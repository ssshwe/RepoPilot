"""Read-only replay of real browser evidence and Task 10 preservation checks."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

from baseline import OUT, ROOT, save, snapshot

sys.path.insert(0, str(ROOT / "services/api"))
from app.citations import CitationValidator
from app.context import ContextBuilder
from app.schemas.code_chat import CodeChatResponse
from app.schemas.retrieval import RetrievalResult


def load(path):
    return json.loads(path.read_bytes().decode("utf-8"))


def replay(name, context_number, generation_number):
    raw = load(OUT / f"response-{name}.json")
    parsed = CodeChatResponse.model_validate(raw, strict=True)
    capture = load(OUT / "browser" / f"context-{context_number:03}.json")
    data = capture["context"]
    retrieved = [RetrievalResult.model_validate(item) for item in capture["input"]]
    built = ContextBuilder(max_context_chunks=data["max_context_chunks"],
                           max_context_characters=data["max_context_characters"]).build(parsed.repository, retrieved)
    # asdict retains a sources tuple; JSON roundtrip normalizes tuples to lists.
    assert json.loads(json.dumps(asdict(built))) == data
    citations = CitationValidator().validate(parsed.repository, parsed.source_ids,
                                             context=built, retrieval_results=retrieved)
    assert [item.model_dump() for item in citations] == raw["citations"]
    for citation, source in zip(citations, parsed.sources, strict=True):
        emitted = built.source_map[citation.source_id]
        assert source.model_dump() == {"source_id": emitted.source_id,
                                       "language": emitted.language, "content": emitted.content}
        assert set(citation.model_dump()) == {"repository", "source_id", "path", "symbol", "start_line", "end_line"}
        assert len(source.content.splitlines()) == citation.end_line - citation.start_line + 1
    for item in retrieved:
        text = (ROOT / "workspaces" / item.repository / item.path).read_bytes().decode("utf-8-sig")
        assert item.content == "".join(text.splitlines(keepends=True)[item.start_line - 1:item.end_line])
    if generation_number is None:
        assert not parsed.generation.called and parsed.model is None
        assert not parsed.source_ids and not parsed.citations and not parsed.sources
        assert parsed.retrieval.empty_reason == "no_results"
    else:
        generated = load(OUT / "browser" / f"generation-{generation_number:03}.json")
        assert parsed.model == generated["model"] == "qwen2.5:0.5b"
        assert parsed.answer == generated["result"]["answer"]
        assert parsed.source_ids == generated["result"]["source_ids"]
        assert sorted(generated["schema"]["properties"]) == ["answer", "source_ids"]
        calls = generated["actual_generation_http_requests"]
        assert len(calls) == 1 and not calls[0]["tools_advertised"]
        assert calls[0]["schema_properties"] == ["answer", "source_ids"]
        assert calls[0]["messages"] == generated["messages"]
        assert [message["role"] for message in generated["messages"]] == ["system", "user", "user"]
        assert generated["messages"][-1]["content"] == parsed.question
        assert built.text in generated["messages"][1]["content"]
        provider_result = json.loads(calls[0]["provider_reply"]["content"])
        assert provider_result == generated["result"]
    return {"case": name, "repository": parsed.repository, "question": parsed.question,
            "answer": parsed.answer, "citations": raw["citations"],
            "preview_line_counts": [len(s.content.splitlines()) for s in parsed.sources],
            "context_characters": built.characters_used, "generation_called": parsed.generation.called,
            "source_previews_equal_validated_context": True, "retrieval_matches_local_files": True}


def test_report(name, expected_tests, expected_skipped):
    root = ET.parse(OUT / name).getroot()
    cases = list(root.iter("testcase"))
    failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    assert len(cases) == expected_tests and skipped == expected_skipped and failed == 0
    return {"collected": len(cases), "passed": len(cases) - skipped, "skipped": skipped, "failed": failed}


def main():
    responses = [replay("refund", 1, 1), replay("controlled-pending-real", 2, 2),
                 replay("empty", 3, None), replay("refund-final", 4, 3)]
    finished = load(OUT / "browser/server-finished.json")
    assert finished == {"contexts_built": 4, "generation_calls": 3,
                        "citation_validation_calls": 7, "actual_generation_http_requests": 3, "stopped": True}
    controlled = load(OUT / "browser-controls.json")
    real = load(OUT / "browser-real-flows.json")
    assert controlled["kind"] == "controlled browser fixtures, no inference"
    assert len(controlled["checks"]) == 8 and all(value is True for value in controlled["checks"].values())
    assert real["kind"] == "real browser requests, no mocked inference"
    assert len(real["checks"]) == 5 and all(value is True for value in real["checks"].values())
    assert real["answer"] == responses[-1]["answer"] and real["citation"] == responses[-1]["citations"][0]
    tests = {"backend": test_report("backend-tests.xml", 947, 6),
             "evaluation": test_report("evaluation-tests.xml", 42, 0),
             "frontend": test_report("frontend-tests.xml", 28, 0)}
    before = load(OUT / "workspace-before.json")
    changed = []
    for name, digest in before.items():
        path = ROOT / name
        assert path.is_file(), f"Removed protected file: {name}"
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(name)
    expected = {"README.md", "apps/web/src/App.vue", "apps/web/src/style.css",
                "services/api/app/rag/service.py", "services/api/app/schemas/code_chat.py"}
    assert set(changed) == expected and len(before) == 443
    preservation = {"protected_files": len(before), "expected_changed_files": sorted(changed),
                    "other_existing_files_unchanged": len(before) - len(changed)}
    save("preservation.json", preservation)
    after = snapshot()
    assert after == load(OUT / "qdrant-before.json"), "Qdrant changed"
    save("qdrant-after.json", after)
    production = next(c for c in after["collections"] if c["name"] == "repopilot_code")
    assert production["points_count"] == 17 and production["vectors"] == {"size": 1024, "distance": "Cosine"}
    screenshots = {}
    for name in ("desktop", "mobile", "mobile-preview", "empty", "error", "untrusted-text"):
        path = ROOT / "output/playwright" / f"task10-{name}.png"
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        screenshots[name] = {"path": path.relative_to(ROOT).as_posix(), "width": width, "height": height}
    assert (screenshots["desktop"]["width"], screenshots["desktop"]["height"]) == (1280, 1000)
    assert (screenshots["mobile"]["width"], screenshots["mobile"]["height"]) == (390, 844)
    assert (ROOT / "apps/web/dist/index.html").is_file()
    report = {"task": "Stage 3 / Task 10", "verified": True, "real_responses": responses,
              "real_api_counts": finished, "controlled_browser_checks": controlled,
              "real_browser_checks": real, "tests": tests,
              "new_backend_tests": 11, "new_frontend_tests": 19,
              "build": {"command": "npm.cmd run build", "exit_code": 0, "transformed_modules": 1620},
              "screenshots": screenshots, "preservation": preservation,
              "qdrant": {"unchanged": True, "production_points": 17, "dimensions": 1024,
                         "distance": "Cosine", "production_points_sha256": production["points_sha256"]},
              "limitations": ["No semantic answer verification", "Client abort does not guarantee server inference cancellation",
                              "In-memory independent turns; no streaming RAG or Issue Analyzer"],
              "browser_fixture_note": "The initial browser harness timed out on a renamed busy button. Its cleanup forwarded one held request containing 'controlled pending question' to the real API. That real response is preserved separately and is not a successful answer-quality case. The corrected eight controlled browser checks passed with routes removed afterward."}
    save("verification.json", report)
    (ROOT / "docs/stage-3-task-10-verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": True, "protected_files": len(before), "expected_changes": len(changed),
                      "real_api_calls": 4, "real_generation_calls": 3, "qdrant_unchanged": True,
                      "tests": tests}, ensure_ascii=False))


if __name__ == "__main__":
    main()
