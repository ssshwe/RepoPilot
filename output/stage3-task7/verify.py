"""Independently verify real HTTP evidence, source text, tests and preservation."""
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from baseline import OUT, ROOT, save, snapshot

sys.path.insert(0, str(ROOT / "services/api"))
from app.main import app
from app.rag.prompt import INSUFFICIENT_EVIDENCE, RAG_SYSTEM_PROMPT
from app.schemas.code_chat import CodeChatResponse

BOUNDARY = re.compile(r"\[SOURCE (S[1-9][0-9]*)\]\n(.*?)\n\[/SOURCE \1\]", re.DOTALL)


def load(path):
    return json.loads(path.read_bytes().decode("utf-8"))


def verify_context(path):
    record = load(path)
    context = record["context"]
    by_id = {item["chunk_id"]: item for item in record["input"]}
    assert context["characters_used"] == len(context["text"]) <= context["max_context_characters"]
    matches = list(BOUNDARY.finditer(context["text"]))
    assert "\n\n".join(match.group(0) for match in matches) == context["text"]
    assert len(matches) == len(context["sources"])
    for source, match in zip(context["sources"], matches, strict=True):
        assert source["source_id"] == match.group(1)
        body = json.loads(match.group(2))
        original = by_id[source["chunk_id"]]
        for key in ("repository", "path", "language", "symbol", "node_type", "parent_symbol", "chunking_strategy", "chunking_version"):
            assert source[key] == body[key] == original[key]
        assert body["lines"] == {"start": source["start_line"], "end": source["end_line"]}
        raw = (ROOT / "workspaces" / source["repository"] / source["path"]).read_bytes().decode("utf-8-sig")
        assert source["content"] == body["content"] == "".join(raw.splitlines(keepends=True)[source["start_line"] - 1:source["end_line"]])
    for item in record["input"]:
        raw = (ROOT / "workspaces" / item["repository"] / item["path"]).read_bytes().decode("utf-8-sig")
        assert item["content"] == "".join(raw.splitlines(keepends=True)[item["start_line"] - 1:item["end_line"]])
    return context


def tests(path):
    root = ET.parse(path).getroot()
    suites = list(root.iter("testsuite"))
    total = {key: sum(int(suite.attrib[key]) for suite in suites) for key in ("tests", "failures", "errors", "skipped")}
    assert total["failures"] == total["errors"] == 0
    total["passed"] = total["tests"] - total["skipped"]
    return total


def verify_round(directory, *, final):
    contexts = [verify_context(directory / f"context-{index:03}.json") for index in range(1, 4)]
    generations = [load(directory / f"generation-{index:03}.json") for index in range(1, 3)]
    requests = [load(directory / f"response-{name}.json") for name in ("refund", "no-index", "unsupported")]
    for request, context in zip(requests, contexts, strict=True):
        assert request["http_status"] == 200
        body = CodeChatResponse.model_validate(request["response"])
        assert body.repository == request["request"]["repository"] == context["repository"]
        assert body.retrieval.context_characters == context["characters_used"]
        assert body.retrieval.used_sources == len(context["sources"])
        assert set(body.source_ids).issubset({s["source_id"] for s in context["sources"]})
    assert requests[0]["request"]["question"] == "退款服务在哪里校验订单编号，然后把退款金额转回钱包？"
    assert requests[1]["response"]["generation"]["called"] is False and contexts[1]["sources"] == []
    assert requests[1]["response"]["answer"] == INSUFFICIENT_EVIDENCE
    for generation, context, request in zip(generations, (contexts[0], contexts[2]), (requests[0], requests[2]), strict=True):
        assert generation["model"] == "qwen2.5:0.5b"
        assert len(generation["actual_generation_http_requests"]) == 1
        assert generation["actual_generation_http_requests"][0]["host"] == "127.0.0.1"
        assert [m["role"] for m in generation["messages"]] == ["system", "user", "user"]
        assert generation["messages"][-1]["content"] == request["request"]["question"]
        assert context["text"] in generation["messages"][1]["content"]
        assert all(len(message["content"]) <= 20000 for message in generation["messages"])
        allowed = generation["schema"]["properties"]["source_ids"]["items"]["enum"]
        assert allowed == [s["source_id"] for s in context["sources"]]
        assert set(generation["result"]["source_ids"]).issubset(allowed)
        if final:
            assert generation["messages"][0]["content"] == RAG_SYSTEM_PROMPT
    finished = load(directory / "server-finished.json")
    assert finished == {"contexts_built": 3, "generation_calls": 2, "actual_generation_http_requests": 2, "stopped": True}
    return requests, contexts, generations


def main():
    initial, initial_contexts, _ = verify_round(OUT, final=False)
    final, contexts, generations = verify_round(OUT / "final", final=True)
    assert initial[2]["response"]["answer"] == "实现位置：未实现"
    assert initial[2]["response"]["source_ids"] == ["S5"]
    unsupported_source = initial_contexts[2]["sources"][4]
    assert unsupported_source["path"] == "README.md" and "PostgreSQL" not in unsupported_source["content"]
    assert final[2]["response"]["answer"] == INSUFFICIENT_EVIDENCE and final[2]["response"]["source_ids"] == []
    assert generations[1]["result"]["source_ids"] == []
    assert final[0]["response"]["answer"] == "backend/service/RefundService.java"
    assert final[0]["response"]["source_ids"] == ["S1"]
    source = contexts[0]["sources"][0]
    assert source["symbol"] == "RefundService.refund" and (source["start_line"], source["end_line"]) == (12, 18)
    assert "wallets.credit(userId, amount);" in source["content"]
    assert contexts[0]["characters_used"] == 4110 and len(contexts[0]["sources"]) == 5

    backend = tests(OUT / "backend-tests.xml")
    evaluation = tests(OUT / "evaluation-tests.xml")
    assert (backend["tests"], backend["passed"], backend["skipped"]) == (790, 784, 6)
    assert evaluation["passed"] == 42
    new_cases = [case for case in ET.parse(OUT / "backend-tests.xml").getroot().iter("testcase")
                 if case.attrib.get("classname", "").endswith("test_code_chat")]
    assert len(new_cases) == 60
    contract = app.openapi()
    operation = contract["paths"]["/api/code-chat"]["post"]
    assert "200" in operation["responses"] and "422" in operation["responses"]
    assert set(contract["components"]["schemas"]["CodeChatRequest"]["properties"]) == {"repository", "question", "top_k"}
    assert contract["components"]["schemas"]["CodeChatRequest"]["additionalProperties"] is False

    after = snapshot()
    assert after == load(OUT / "qdrant-before.json")
    save("qdrant-after.json", after)
    old = load(OUT / "workspace-before.json")
    changed = sorted(name for name, digest in old.items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest)
    assert changed == ["README.md", "services/api/app/core/exceptions.py", "services/api/app/main.py"]
    production = next(c for c in after["collections"] if c["name"] == "repopilot_code")
    preservation = {"protected_files": len(old), "changed_existing_files": changed, "unexpected_changes": [],
                    "env_unchanged": True, "lockfiles_unchanged": True, "fixture_dataset_and_past_reports_unchanged": True,
                    "frontend_unchanged": True, "task6_context_builder_unchanged": True,
                    "qdrant_exact_match": True, "production_points": production["points_count"],
                    "production_points_sha256": production["points_sha256"]}
    save("preservation.json", preservation)
    bad_case = {"question": initial[2]["request"]["question"], "initial": initial[2]["response"],
                "initial_cited_source": {k: unsupported_source[k] for k in ("source_id", "path", "symbol", "start_line", "end_line")},
                "problem": "A valid ID and a README without PostgreSQL evidence cannot support a repository-wide absence claim.",
                "change": "Prompt explicitly forbids inferring repository-wide nonexistence from partial context.",
                "final": final[2]["response"], "full_source_text_verified": True,
                "remaining_refund_quality_issue": "The final refund response still only names the real file, without explaining the code."}
    save("bad-case.json", bad_case)
    evidence = {
        "task": "Stage 3 / Task 7: RAG Backend", "status": "complete", "date": "2026-10-07", "timezone": "Asia/Shanghai",
        "backend": {**backend, "new_code_chat_cases": 60, "warnings": 2}, "evaluation_tests": evaluation,
        "frontend": {"tests_passed": 9, "typecheck_and_build": "passed", "modules": 1615},
        "endpoint": "POST /api/code-chat", "output_stage": "answer + Source IDs; final Citations are Task 8",
        "llm_model": "qwen2.5:0.5b", "embedding_model": "qwen3-embedding:0.6b", "embedding_dimension": 1024,
        "generation_policy": "One structured Provider call per nonempty context; no application repair/agent loop",
        "real_http_acceptance": {"rounds": 2, "api_requests": 6, "generation_calls": 4,
                                 "actual_generation_http_requests": 4, "sources_verified_against_local_files": True,
                                 "final_refund": final[0]["response"], "final_no_index": final[1]["response"],
                                 "final_unsupported": final[2]["response"],
                                 "refund_source_metadata": {k: source[k] for k in ("source_id", "chunk_id", "repository", "path", "symbol", "node_type", "parent_symbol", "start_line", "end_line", "chunking_strategy")}},
        "guards": ["Strict request validation before factories", "Full context scope validation before LLM resolution",
                   "Dynamic Source ID enum and strict result revalidation", "Empty context skips LLM",
                   "Empty generated IDs replace unsupported text with controlled abstention",
                   "Message envelope reserved within existing ChatMessage limit", "Untrusted context in user role",
                   "Safe 422 for invalid Unicode and safe 502 for invalid generation"],
        "bad_case": bad_case, "preservation": preservation,
        "temporary_api_stopped": True, "qdrant_and_ollama_kept_running": True,
        "not_implemented": ["CitationValidator and mapped Citations", "Code Chat UI", "Issue Analyzer", "Agent loop", "Hybrid/Rerank"],
        "next_task": "Task 8: Citation Validator",
    }
    (ROOT / "docs/stage-3-task-7-verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": True, "backend_passed": backend["passed"], "new_cases": len(new_cases),
                      "protected_files": len(old), "changed_existing_files": changed,
                      "real_api_requests": 6, "real_generation_requests": 4,
                      "production_points": production["points_count"], "exact_index_match": True}, indent=2))


if __name__ == "__main__":
    main()
