"""Read-only replay of Task 12 reports and preservation; never rerun inference or write an index."""
from dataclasses import asdict
import hashlib
import json
import sys
import xml.etree.ElementTree as ET

from baseline import OUT, ROOT, save, snapshot

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/api"))
from evaluation.stage3.evaluate import fingerprint, load_grounding_dataset, measure_retrieval, summarize_retrieval, verify_response_evidence
from evaluation.stage3.review import review_report
from app.context import ContextBuilder
from app.issues.prompt import build_issue_messages, issue_result_model
from app.llm.compatible_provider import _provider_json_schema
from app.rag.prompt import build_rag_messages, repository_answer_model
from app.schemas.retrieval import RetrievalResult
from qdrant_client.models import Record

REPORTS = ROOT / "evaluation/reports"
RAW = REPORTS / "stage3_grounding_20261012_task12_raw.json"
REVIEW = REPORTS / "stage3_grounding_20261012_task12_review.json"
SUMMARY = REPORTS / "stage3_grounding_20261012_task12_summary.json"


def load(path):
    return json.loads(path.read_bytes())


def main():
    data, digest, base = load_grounding_dataset(ROOT / "evaluation/datasets/stage3_grounding.json", ROOT / "workspaces")
    report = load(RAW)
    assert report["dataset_sha256"] == digest and report["source_sha256"] == data["source_sha256"]
    assert report["query_count"] == len(report["cases"]) == len(data["cases"]) == 16
    assert report["kind"] == "real FastAPI ASGI evaluation; actual embedding and LLM HTTP, no mocked inference"
    assert report["configuration"] == {"collection": "repopilot_code", "embedding_model": "qwen3-embedding:0.6b",
        "llm_model": "qwen2.5:0.5b", "max_context_chunks": 5, "max_context_characters": 16000, "top_k": 5}
    retrieval_rows, answer_rows, citation_rows = [], [], []
    for index, (case, row) in enumerate(zip(data["cases"], report["cases"], strict=True), 1):
        assert row["id"] == case["id"] and row["group"] == case["group"] and row["mode"] == case["mode"]
        assert load(REPORTS / f"{RAW.stem}_cases" / f"{index:02}-{case['id']}.json") == row
        trace = row["trace"]
        check = verify_response_evidence(case, row["response"], trace, base.repository_root)
        assert check == row["citation_check"]
        metrics = measure_retrieval(case, trace["retrieval"][0]["results"], trace["contexts"][0])
        assert metrics == row["retrieval_metrics"]
        recorded = trace["contexts"][0]
        built = ContextBuilder(max_context_chunks=recorded["max_context_chunks"], max_context_characters=recorded["max_context_characters"]).build(
            case["repository"], [RetrievalResult.model_validate(r, strict=True) for r in trace["retrieval"][0]["results"]])
        assert json.loads(json.dumps(asdict(built))) == recorded
        raw = row["response"]["body"]
        assert row["response"]["http_status"] == 200
        assert len(trace["validations"]) == 2 if built.sources else len(trace["validations"]) == 1
        assert trace["validations"][0] == {"repository": case["repository"], "source_ids": [], "citations": []}
        if raw["generation"]["called"]:
            generation, call = trace["generations"][0], trace["network_calls"][0]
            assert len(trace["generations"]) == len(trace["network_calls"]) == 1
            if case["mode"] == "code_chat":
                model, messages = repository_answer_model(built), build_rag_messages(case["query"], built)
                ids, citations = raw["source_ids"], raw["citations"]
            else:
                model, messages = issue_result_model(built), build_issue_messages(case["query"], built)
                ids, citations = raw["source_ids"], raw["related_code"]
            assert generation["schema"] == model.model_json_schema()
            assert generation["messages"] == [m.model_dump() for m in messages] == call["messages"]
            assert call["schema"] == _provider_json_schema(generation["schema"])
            assert call["host"] == "127.0.0.1" and call["path"] == "/v1/chat/completions"
            assert call["model"] == generation["model"] == raw["model"] == "qwen2.5:0.5b"
            assert call["tools_advertised"] is False and "tools" not in call["request_fields"]
            assert call["temperature"] == 0 and call["max_tokens"] == 512
            assert call["reply"]["http_status"] == 200 and call["reply"]["finish_reason"] == "stop"
            result = model.model_validate_json(call["reply"]["content"], strict=True).model_dump(mode="json")
            assert result == generation["result"]
            assert ids == result["source_ids"]
            if case["mode"] == "code_chat":
                assert raw["answer"] == result["answer"]
            else:
                assert all(raw[f] == result[f] for f in ("summary", "analysis", "modification_suggestions", "test_suggestions", "risks"))
                by_id = {c["source_id"]: c for c in citations}
                assert raw["possible_call_chain"] == [by_id[s] for s in result["possible_call_chain"]]
            assert trace["validations"][1] == {"repository": case["repository"], "source_ids": ids, "citations": citations}
        else:
            assert not trace["network_calls"] and not trace["generations"]
            assert raw["model"] is None and raw["status"] == "insufficient_evidence" and not raw["sources"]
        retrieval_rows.append({"id": case["id"], "query": case["query"], "group": case["group"],
            "expected_paths": case["expected_paths"], "expected_symbols": case["expected_symbols"], "evidence": case["evidence"],
            "results": [{k: r[k] for k in ("rank", "score", "path", "symbol", "node_type", "start_line", "end_line", "chunking_strategy", "chunking_version")}
                        for r in trace["retrieval"][0]["results"]], "metrics": metrics,
            "context": {"characters": built.characters_used, "budget": built.max_context_characters,
                        "budget_limited": built.budget_limited, "sources": [{k: s[k] for k in ("source_id", "path", "symbol", "start_line", "end_line", "budget_truncated")} for s in recorded["sources"]]}})
        answer_rows.append({"id": case["id"], "query": case["query"], "expected_answer": case["expected_answer"],
            "response": {k: v for k, v in raw.items() if k in ("answer", "summary", "analysis", "modification_suggestions", "test_suggestions", "risks", "source_ids", "status")}})
        citation_rows.append({"id": case["id"], "metadata_check": check, "citations": raw.get("citations", raw.get("related_code", [])),
            "preview_line_counts": [len(s["content"].splitlines()) for s in raw["sources"]], "possible_call_chain": raw.get("possible_call_chain")})
    assert summarize_retrieval(report["cases"]) == report["retrieval_summary"]
    assert sum(len(r["trace"]["network_calls"]) for r in report["cases"]) == report["native_generation_http_calls"] == 15
    recomputed = review_report(RAW, REVIEW)
    assert recomputed == load(SUMMARY)
    reviews = {j["id"]: j for j in recomputed["reviews"]}
    for row in answer_rows:
        row["review"] = reviews[row["id"]]
    for row in citation_rows:
        row["citation_batch_supports_answer"] = reviews[row["id"]]["citation_batch_supports_answer"]
        row["candidate_chain_supported"] = reviews[row["id"]]["candidate_chain_supported"]
    save("retrieval-cases.json", {"kind": "actual recorded retrieval/context", "cases": retrieval_rows})
    save("answer-cases.json", {"kind": "actual replies plus explicit development-assistant source review", "independent_human_review": "pending", "cases": answer_rows})
    save("citation-cases.json", {"kind": "replayed coordinates/previews and separate semantic review", "cases": citation_rows})
    annotations = [
        ("refund-controller", "Dense Symbol miss / small AST fragments", "File Hit@1=true, but imports 1–5 and class fields 6–8 displace refundOrder 13–15. The indexed method appears in refund-service Rank 3, proving it exists. This run does not prove a larger chunk would fix ranking."),
        ("wallets-schema", "Dense file miss / wrong answer scope", "schema.sql is absent from Top5; model answers with a Java balance-reading method instead of the table default."),
        ("refund-call-chain", "Incomplete call evidence", "Top5 covers only 1/3 annotated methods; controller imports and unrelated types have valid coordinates but do not establish a full chain."),
        ("wallet-default-balance", "False absence despite exact method hit", "Rank 1 contains getOrDefault(userId, BigDecimal.ZERO); actual answer says no zero return found."),
        ("orders-schema", "Wrong answer despite correct Citation", "S1 is actual schema.sql including orders.status and amount, but answer claims both are defined in RefundService.java."),
        ("fixture-documentation", "Wrong selected evidence", "The answer names a file but cites config/repository.json; README S2 contains relevant documentation and was not selected."),
        ("issue-direct", "Non-callable candidate node / test advice misclassified", "The only candidate node is README; test_suggestions describes a code change rather than a concrete repeat-call scenario."),
        ("refund-budget700", "Context insufficient after real truncation", "S1 is 12–15 rather than original 12–18; wallets.credit at line17 is absent. Citation truncation is correct; answer only repeats the question."),
        ("issue-refund", "Unsupported truncation explanation", "Model says code was truncated although all emitted budget_truncated flags and budget_limited are false, and the refund method is complete."),
    ]
    bad_cases = []
    raw_by_id = {r["id"]: r for r in report["cases"]}
    for identifier, category, observation in annotations:
        bad_cases.append({"id": identifier, "category": category, "observation": observation,
                          "review": reviews[identifier], "actual_response": raw_by_id[identifier]["response"],
                          "scope": "recorded only; no retrieval, chunker, prompt or provider repair in Task 12"})
    save("bad-cases.json", {"kind": "real failures, source-reviewed by development assistant", "cases": bad_cases,
                            "large_ast_chunk_case": "Not observed in this small fixture; no synthetic case claimed as a real result",
                            "stage4_note": "Search/chunk-size investigations are recorded, not implemented here"})
    junit = ET.parse(OUT / "evaluation-tests.xml").getroot()
    cases = list(junit.iter("testcase"))
    assert len(cases) == 61 and all(c.find("failure") is None and c.find("error") is None and c.find("skipped") is None for c in cases)
    new = sum("test_grounding_evaluation" in c.attrib.get("classname", "") for c in cases)
    assert new == 19
    before = load(OUT / "workspace-before.json")
    changed = []
    for name, digest in before.items():
        path = ROOT / name
        assert path.is_file(), f"Missing protected file: {name}"
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(name)
    assert len(before) == 569 and set(changed) == {"README.md", "evaluation/README.md"}
    preservation = {"protected_files": len(before), "expected_changed_files": sorted(changed), "other_existing_files_unchanged": len(before) - len(changed)}
    save("preservation.json", preservation)
    after = snapshot()
    assert after == load(OUT / "qdrant-before.json"), "Persistent index changed"
    save("qdrant-after.json", after)
    for collection in after["collections"]:
        # The historical REST snapshot omits optional null Record fields; the
        # SDK snapshot includes them. Normalize through the same SDK model.
        points = [Record.model_validate(p).model_dump(mode="json") for p in collection["points"]]
        assert fingerprint(points) == report["qdrant_before"][collection["name"]]["points_sha256"]
    canonical = recomputed["review_summary"]["canonical"]
    assert canonical["supported_answers"] == 5 and canonical["complete_substantive_answers"] == 3
    assert recomputed["review_summary"]["all"]["citation_metadata"] == {"valid_batches": 15, "cited_batches": 15, "citations": 46, "non_applicable_batches": 1}
    verified = {"task": "Stage 3 / Task 12", "evaluation_completed": True, "answer_quality_accepted": False,
                "run_date": "2026-10-12", "transport": report["kind"], "real_requests": 16, "native_generation_http_calls": 15,
                "retrieval_summary": report["retrieval_summary"], "review_summary": recomputed["review_summary"],
                "reviewer": recomputed["reviewer"], "dataset_sha256": report["dataset_sha256"],
                "raw_report": RAW.relative_to(ROOT).as_posix(), "review_report": REVIEW.relative_to(ROOT).as_posix(),
                "summary_report": SUMMARY.relative_to(ROOT).as_posix(), "evidence_replay_passed": True,
                "tests": {"evaluation_passed": 61, "existing": 42, "new": 19, "inference_in_unit_tests": False},
                "preservation": preservation, "qdrant": {"unchanged": True, "production_points": 17, "dimensions": 1024, "distance": "Cosine"},
                "bad_case_count": len(bad_cases), "production_code_changed": False, "model_or_environment_changed": False,
                "next_task": "Task 13 final Stage 3 testing/documentation (not started)"}
    save("verification.json", verified)
    (ROOT / "docs/stage-3-task-12-verification.json").write_text(json.dumps(verified, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evaluation_completed": True, "answer_quality_accepted": False, "requests": 16, "native_calls": 15,
                      "canonical_supported": 5, "canonical_complete": 3, "valid_citations": 46, "offline_tests": 61,
                      "protected_files": len(before), "other_existing_files_unchanged": len(before) - len(changed), "qdrant_unchanged": True}))


if __name__ == "__main__":
    main()
