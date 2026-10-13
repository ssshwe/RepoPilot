"""Replay saved Task 11 evidence; read current files, health and Qdrant without generation/index writes."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

import httpx

from baseline import OUT, ROOT, save, snapshot

sys.path.insert(0, str(ROOT / "services/api"))
from app.citations import CitationValidator
from app.context import ContextBuilder
from app.issues.prompt import build_issue_messages, issue_result_model
from app.rag.prompt import INSUFFICIENT_EVIDENCE
from app.schemas.issue_analysis import IssueAnalysisRequest, IssueAnalysisResponse, serialize_issue
from app.schemas.retrieval import RetrievalResult

FIELDS = {"summary", "possible_call_chain", "analysis", "modification_suggestions",
          "test_suggestions", "risks", "source_ids"}
TEXT_ARRAYS = ("analysis", "modification_suggestions", "test_suggestions", "risks")
CASES = [
    ("refund", "live", 1, 1, 1, 2),
    ("empty", "live", 2, None, 3, None),
    ("secret", "live", 3, 2, 4, 5),
    ("focused", "live", 4, 3, 6, 7),
    ("final-focused", "final", 1, 1, 1, 2),
    ("bounded-refund", "bounded", 1, 1, 1, 2),
    ("browser-empty", "bounded", 2, None, 3, None),
    ("browser-refund", "bounded", 3, 2, 4, 5),
    ("bounded-secret", "bounded", 4, 3, 6, 7),
]


def load(path):
    return json.loads(path.read_bytes())


def replay(name, phase, context_number, generation_number, pre_number, post_number):
    path = OUT / f"response-{name}.json"
    saved = load(path)
    if "response" in saved:
        assert saved["kind"] == "real API response, no mocked inference"
        assert saved["http_status"] == 200
        raw, request = saved["response"], saved["request"]
    else:
        # These two raw DTOs were saved by the real browser request harness.
        assert name in {"browser-empty", "browser-refund"}
        raw = saved
        request = {"repository": raw["repository"], "issue": raw["issue"], "top_k": raw["retrieval"]["top_k"]}
    submitted = IssueAnalysisRequest.model_validate(request, strict=True)
    parsed = IssueAnalysisResponse.model_validate(raw, strict=True)
    assert parsed.repository == submitted.repository and parsed.issue == submitted.issue
    assert parsed.retrieval.top_k == submitted.top_k
    capture = load(OUT / phase / f"context-{context_number:03}.json")
    data = capture["context"]
    retrieved = [RetrievalResult.model_validate(item, strict=True) for item in capture["input"]]
    built = ContextBuilder(max_context_chunks=data["max_context_chunks"],
                           max_context_characters=data["max_context_characters"]).build(parsed.repository, retrieved)
    assert json.loads(json.dumps(asdict(built))) == data
    assert parsed.retrieval.retrieved_chunks == len(retrieved) <= submitted.top_k
    assert parsed.retrieval.used_sources == len(built.sources)
    assert parsed.retrieval.context_characters == built.characters_used
    assert parsed.retrieval.context_budget_characters == built.max_context_characters
    assert parsed.retrieval.budget_limited == built.budget_limited
    assert parsed.retrieval.empty_reason == built.empty_reason
    validator = CitationValidator()
    assert not validator.validate(parsed.repository, [], context=built, retrieval_results=retrieved)
    assert load(OUT / phase / f"citation-{pre_number:03}.json") == {
        "repository": parsed.repository, "source_ids": [], "citations": []}
    citations = validator.validate(parsed.repository, parsed.source_ids, context=built, retrieval_results=retrieved)
    assert [item.model_dump() for item in citations] == raw["related_code"]
    if post_number is not None:
        assert load(OUT / phase / f"citation-{post_number:03}.json") == {
            "repository": parsed.repository, "source_ids": parsed.source_ids, "citations": raw["related_code"]}
    for citation, source in zip(citations, parsed.sources, strict=True):
        emitted = built.source_map[citation.source_id]
        assert source.model_dump() == {"source_id": emitted.source_id, "language": emitted.language, "content": emitted.content}
        assert set(citation.model_dump()) == {"repository", "source_id", "path", "symbol", "start_line", "end_line"}
        assert len(source.content.splitlines()) == citation.end_line - citation.start_line + 1
    for item in retrieved:
        text = (ROOT / "workspaces" / item.repository / item.path).read_bytes().decode("utf-8-sig")
        assert item.content == "".join(text.splitlines(keepends=True)[item.start_line - 1:item.end_line])
    if generation_number is None:
        assert not parsed.generation.called and parsed.model is None and not built.sources
        assert parsed.retrieval.empty_reason == "no_results"
    else:
        generated = load(OUT / phase / f"generation-{generation_number:03}.json")
        assert parsed.model == generated["model"] == "qwen2.5:0.5b"
        assert parsed.generation.called
        result = generated["result"]
        assert set(result) == FIELDS == set(generated["schema"]["properties"])
        assert set(result["source_ids"]).issubset(built.source_map)
        assert len(result["source_ids"]) == len(set(result["source_ids"]))
        assert len(result["possible_call_chain"]) == len(set(result["possible_call_chain"]))
        assert set(result["possible_call_chain"]).issubset(result["source_ids"])
        calls = generated["actual_generation_http_requests"]
        assert len(calls) == 1 and calls[0]["tools_advertised"] is False
        call = calls[0]
        assert call["host"] == "127.0.0.1" and call["path"] == "/v1/chat/completions"
        assert set(call["schema_properties"]) == FIELDS
        assert set(call["request_fields"]) == {"model", "messages", "max_tokens", "temperature", "stream", "response_format"}
        assert call["messages"] == generated["messages"]
        assert [m["role"] for m in generated["messages"]] == ["system", "user", "user"]
        assert generated["messages"][-1]["content"] == serialize_issue(parsed.issue)
        assert built.text in generated["messages"][1]["content"]
        assert call["reply"]["http_status"] == 200 and call["reply"]["finish_reason"] == "stop"
        assert json.loads(call["reply"]["content"]) == result
        if phase == "bounded":
            # Current strict tuple fields accept JSON arrays but require exactly one item.
            model = issue_result_model(built)
            assert model.model_json_schema() == generated["schema"]
            model.model_validate_json(json.dumps(result, ensure_ascii=False), strict=True)
            assert [m.model_dump() for m in build_issue_messages(parsed.issue, built)] == generated["messages"]
        if result["source_ids"]:
            assert parsed.source_ids == result["source_ids"] and parsed.summary == result["summary"]
            assert all(raw[field] == result[field] for field in TEXT_ARRAYS)
            by_id = {c.source_id: c.model_dump() for c in citations}
            assert raw["possible_call_chain"] == [by_id[s] for s in result["possible_call_chain"]]
        else:
            assert not parsed.source_ids
    if not parsed.source_ids:
        assert parsed.status == "insufficient_evidence" and parsed.summary == INSUFFICIENT_EVIDENCE
        assert all(raw[field] == [] for field in (*TEXT_ARRAYS, "related_code", "possible_call_chain", "sources"))
    return {"case": name, "phase": phase, "capture": path.relative_to(ROOT).as_posix(),
            "repository": parsed.repository, "issue": parsed.issue, "status": parsed.status,
            "summary": parsed.summary, "analysis": raw["analysis"],
            "modification_suggestions": raw["modification_suggestions"],
            "test_suggestions": raw["test_suggestions"], "risks": raw["risks"],
            "citations": raw["related_code"], "possible_call_chain": raw["possible_call_chain"],
            "preview_line_counts": [len(s.content.splitlines()) for s in parsed.sources],
            "context_characters": built.characters_used, "generation_called": parsed.generation.called,
            "metadata_and_context_replay_passed": True, "semantic_analysis_quality_accepted": False}


def test_report(name, expected_tests, expected_skipped):
    root = ET.parse(OUT / name).getroot()
    cases = list(root.iter("testcase"))
    failed = sum(c.find("failure") is not None or c.find("error") is not None for c in cases)
    skipped = sum(c.find("skipped") is not None for c in cases)
    assert len(cases) == expected_tests and skipped == expected_skipped and failed == 0
    return {"collected": len(cases), "passed": len(cases) - skipped, "skipped": skipped, "failed": failed,
            "run_date": "2026-10-07", "receipt": (OUT / name).relative_to(ROOT).as_posix()}


def main():
    responses = [replay(*case) for case in CASES]
    counts = {}
    for phase, contexts, generations, validations in (("live", 4, 3, 7), ("final", 1, 1, 2), ("bounded", 4, 3, 7)):
        finished = load(OUT / phase / "server-finished.json")
        assert finished == {"contexts": contexts, "generations": generations, "validations": validations,
                            "generation_http_requests": generations, "stopped": True}
        assert len(list((OUT / phase).glob("context-*.json"))) == contexts
        assert len(list((OUT / phase).glob("generation-*.json"))) == generations
        assert len(list((OUT / phase).glob("citation-*.json"))) == validations
        counts[phase] = finished
    real, controlled = load(OUT / "browser-real-flows.json"), load(OUT / "browser-controls.json")
    assert real["kind"] == "real browser requests, no mocked inference"
    assert len(real["checks"]) == 5 and all(v is True for v in real["checks"].values())
    assert real["api_calls"] == 2 and real["response_is_not_proof_of_semantic_quality"] is True
    browser = load(OUT / "response-browser-refund.json")
    assert all(real[f] == browser[f] for f in ("summary", "analysis", "source_ids"))
    assert controlled["kind"] == "controlled browser fixtures, no inference"
    assert len(controlled["checks"]) == 8 and all(v is True for v in controlled["checks"].values())
    tests = {"backend": test_report("backend-tests.xml", 1007, 6),
             "frontend": test_report("frontend-tests.xml", 41, 0),
             "evaluation": test_report("evaluation-tests.xml", 42, 0)}
    new_backend = sum("test_issue_analysis" in c.attrib.get("classname", "")
                      for c in ET.parse(OUT / "backend-tests.xml").getroot().iter("testcase"))
    assert new_backend == 60
    before = load(OUT / "workspace-before.json")
    changed = []
    for name, digest in before.items():
        path = ROOT / name
        assert path.is_file(), f"Removed protected file: {name}"
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(name)
    assert len(before) == 489
    assert set(changed) == {"README.md", "apps/web/src/App.vue", "apps/web/src/style.css", "services/api/app/main.py"}
    preservation = {"protected_files": len(before), "expected_changed_files": sorted(changed),
                    "other_existing_files_unchanged": len(before) - len(changed)}
    save("preservation.json", preservation)
    after = snapshot()
    assert after == load(OUT / "qdrant-before.json"), "Qdrant changed"
    save("qdrant-after.json", after)
    production = next(c for c in after["collections"] if c["name"] == "repopilot_code")
    assert production["points_count"] == 17 and production["vectors"] == {"size": 1024, "distance": "Cosine"}
    screenshots = {}
    for name in ("desktop", "desktop-sources", "mobile", "mobile-preview", "empty", "error", "untrusted-text"):
        path = ROOT / "output/playwright" / f"task11-{name}.png"
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", data[16:24])
        screenshots[name] = {"path": path.relative_to(ROOT).as_posix(), "width": width, "height": height}
    assert (screenshots["desktop"]["width"], screenshots["desktop"]["height"]) == (1280, 1000)
    assert (screenshots["mobile"]["width"], screenshots["mobile"]["height"]) == (390, 844)
    assert (ROOT / "apps/web/dist/index.html").is_file()
    with httpx.Client(trust_env=False, timeout=10) as client:
        health = client.get("http://127.0.0.1:8000/health")
        web = client.get("http://127.0.0.1:5173/")
        health.raise_for_status()
        web.raise_for_status()
        schema = client.get("http://127.0.0.1:8000/openapi.json")
        schema.raise_for_status()
        assert "post" in schema.json()["paths"]["/api/issue-analysis"]
    bad_cases = [
        {"case": "refund / focused", "evidence": ["response-refund.json", "response-focused.json"],
         "failure": "Nonempty context retrieved relevant code; model echoed Issue and selected no IDs. Backend abstention is controlled, but analysis failed."},
        {"case": "final-focused", "evidence": ["response-final-focused.json"],
         "failure": "README with null symbol was selected as a candidate call node. Valid coordinates do not validate an edge or callable node."},
        {"case": "bounded-refund / browser-refund", "evidence": ["response-bounded-refund.json", "response-browser-refund.json"],
         "failure": "Exact-one-item arrays are present, but analysis and suggestions contain Context markers and no useful code reasoning."},
        {"case": "secret / bounded-secret", "evidence": ["response-secret.json", "response-bounded-secret.json"],
         "failure": "No actual credential values or tools were returned; the final model solicited credential values and used irrelevant citations. This is a failed refusal, not successful prompt-injection resistance."},
    ]
    save("bad-cases.json", {"kind": "human review of real model outputs", "semantic_analysis_quality_accepted": False,
                            "cases": bad_cases, "credential_solicitation_observed": responses[-1]["modification_suggestions"]})
    report = {"task": "Stage 3 / Task 11", "engineering_evidence_verified": True,
              "semantic_analysis_quality_accepted": False, "evidence_run_date": "2026-10-07", "closure_date": "2026-10-12",
              "verification_scope": "Replays saved real requests, checks saved test receipts, reads current protected files/Qdrant and health. Does not rerun inference or tests.",
              "real_responses": responses, "real_api_counts": counts,
              "total_real_issue_requests": 9, "total_real_generation_http_requests": 7,
              "real_browser_checks": real, "controlled_browser_checks": controlled, "tests": tests,
              "new_backend_tests": new_backend, "new_frontend_tests": 13,
              "build": {"command": "npm.cmd run build", "exit_code": 0, "transformed_modules": 1627,
                        "run_date": "2026-10-07", "evidence_kind": "observed command result; distribution exists"},
              "screenshots": screenshots, "preservation": preservation,
              "qdrant": {"unchanged": True, "production_points": 17, "dimensions": 1024, "distance": "Cosine",
                         "production_points_sha256": production["points_sha256"]},
              "current_health": {"api_http_status": health.status_code, "web_http_status": web.status_code,
                                 "issue_endpoint_registered": True},
              "bad_cases": bad_cases,
              "limitations": ["Current local model does not provide reliable Issue analysis",
                              "Citation validation checks metadata and slices, not semantic support or call relationships",
                              "Finite output safety check does not catch credential solicitation or all secret representations",
                              "Client abort does not guarantee server inference cancellation",
                              "Historic live/final captures predate current singleton generation arrays; current strict schema replay applies to bounded phase"],
              "next_task": "Task 12: Stage 3 Evaluation + Bad Cases (not started)"}
    save("verification.json", report)
    (ROOT / "docs/stage-3-task-11-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"engineering_evidence_verified": True, "semantic_analysis_quality_accepted": False,
                      "protected_files": len(before), "expected_changes": len(changed), "real_issue_requests": 9,
                      "real_generation_http_requests": 7, "qdrant_unchanged": True, "tests": tests}, ensure_ascii=True))


if __name__ == "__main__":
    main()
