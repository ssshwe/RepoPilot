"""Real Stage 3 evaluation through existing FastAPI ASGI routes; no index writes or LLM judge."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import mean
import sys

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "services/api"))
from evaluation.retrieval.evaluate import EvaluationError, load_dataset, metrics_for_case, require, source_file, verify_source_hashes
from evaluation.retrieval.compare_chunking import evidence_metrics, snapshot
from app.api import code_chat, issues
from app.citations import CitationValidator
from app.context import ContextBuilder
from app.core.config import get_settings
from app.llm.factory import get_llm_provider
from app.main import app
from app.retrieval.service import RetrievalService
from app.retrieval.vector_store import create_qdrant_client
from app.schemas.code_chat import CodeChatRequest, CodeChatResponse
from app.schemas.issue_analysis import IssueAnalysisRequest, IssueAnalysisResponse
from app.schemas.retrieval import RetrievalResult


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def load_grounding_dataset(path, workspaces):
    raw = path.read_bytes()
    require(len(raw) <= 1_048_576, "Dataset exceeds 1 MiB")
    data = json.loads(raw)
    require(data.get("schema_version") == 1 and type(data["schema_version"]) is int, "Unsupported grounding dataset")
    original = ROOT / "evaluation/datasets/stage2_retrieval.json"
    require(data.get("source_dataset_sha256") == hashlib.sha256(original.read_bytes()).hexdigest(), "Stage 2 labels changed")
    base = load_dataset(original, workspaces)
    require(data.get("source_sha256") == base.data["source_sha256"] and data.get("repository") == base.data["repository"], "Source scope changed")
    cases = data.get("cases")
    require(isinstance(cases, list) and 1 <= len(cases) <= 100, "Expected 1..100 cases")
    seen = set()
    canonical = []
    for case in cases:
        require(isinstance(case, dict) and isinstance(case.get("id"), str) and case["id"] and case["id"] not in seen, "Invalid/duplicate case ID")
        seen.add(case["id"])
        require(case.get("mode") in {"code_chat", "issue_analysis"} and case.get("group") in {"canonical", "supplementary"}, "Invalid case mode/group")
        query_field = "question" if case["mode"] == "code_chat" else "issue"
        request_model = CodeChatRequest if case["mode"] == "code_chat" else IssueAnalysisRequest
        request_model.model_validate({"repository": case["repository"], query_field: case["query"], "top_k": 5}, strict=True)
        require(isinstance(case.get("expected_answer"), str) and bool(case["expected_answer"].strip()), "Answer rubric required")
        budget = case.get("context_budget_characters")
        require(budget is None or type(budget) is int and 1 <= budget <= 200_000, "Invalid explicit evaluation budget")
        paths, evidence, symbols = case.get("expected_paths"), case.get("evidence"), case.get("expected_symbols")
        require(isinstance(paths, list) and all(isinstance(p, str) for p in paths) and len(paths) == len(set(paths)), "Invalid expected paths")
        require(isinstance(evidence, list) and len(evidence) == len(paths) and {e["path"] for e in evidence} == set(paths), "Evidence must cover expected paths")
        require(isinstance(symbols, list) and all(set(s) == {"path", "symbol"} and s["path"] in paths and isinstance(s["symbol"], str) and s["symbol"] for s in symbols), "Invalid expected symbols")
        require(len(symbols) == len({(s["path"], s["symbol"]) for s in symbols}), "Duplicate expected symbol")
        require(case["repository"] == data["repository"] or not paths and not evidence and not symbols, "Foreign source labels")
        for entry in evidence:
            require(entry["path"] in data["source_sha256"], "Unfingerprinted source")
            lines = source_file(base.repository_root, entry["path"]).read_text(encoding="utf-8-sig").splitlines(keepends=True)
            start, end = entry["start_line"], entry["end_line"]
            require(type(start) is int and type(end) is int and 1 <= start <= end <= len(lines), "Invalid evidence range")
            require(isinstance(entry["snippet"], str) and bool(entry["snippet"].strip()) and entry["snippet"] in "".join(lines[start - 1:end]), "Evidence does not match source")
        if case["group"] == "canonical":
            require(case["mode"] == "code_chat" and budget is None and case["repository"] == data["repository"], "Canonical settings changed")
            canonical.append({k: case[k] for k in ("id", "query", "expected_paths", "evidence")})
    require(canonical == base.data["cases"], "Canonical queries/labels/order must equal unchanged Stage 2 dataset")
    return data, hashlib.sha256(raw).hexdigest(), base


def measure_retrieval(case, retrieved, context):
    if not case["expected_paths"]:
        return {"file": None, "annotated_lines": None, "symbols": None, "context_annotated_lines": None}
    expected_symbols = {(s["path"], s["symbol"]) for s in case["expected_symbols"]}
    symbolic = {}
    if expected_symbols:
        for k in (1, 3, 5):
            found = expected_symbols.intersection((r["path"], r["symbol"]) for r in retrieved[:k])
            symbolic[str(k)] = {"hit": bool(found), "recall": len(found) / len(expected_symbols),
                                "matched_symbols": [dict(path=p, symbol=s) for p, s in sorted(found)]}
    return {"file": metrics_for_case([r["path"] for r in retrieved], case["expected_paths"]),
            "annotated_lines": evidence_metrics(retrieved, case["evidence"]),
            "symbols": symbolic or None,
            "context_annotated_lines": evidence_metrics(context["sources"], case["evidence"])["5"]}


def summarize_retrieval(rows):
    canonical = [r for r in rows if r["group"] == "canonical"]
    require(len(canonical) == 12, "Canonical denominator must contain all 12 queries")
    summary = {"queries": len(canonical)}
    for category in ("file", "annotated_lines", "symbols"):
        selected = [r for r in canonical if r["retrieval_metrics"][category] is not None]
        summary[category] = {"labelled_queries": len(selected), "at_k": {str(k): {
            "hits": sum(r["retrieval_metrics"][category][str(k)]["hit"] for r in selected),
            "total": len(selected), "hit_rate": mean(r["retrieval_metrics"][category][str(k)]["hit"] for r in selected),
            "macro_recall": mean(r["retrieval_metrics"][category][str(k)]["recall"] for r in selected),
        } for k in (1, 3, 5)} if selected else {}}
    summary["context_full_annotated_coverage"] = {
        "hits": sum(r["retrieval_metrics"]["context_annotated_lines"]["recall"] == 1 for r in canonical),
        "total": len(canonical),
        "macro_recall": mean(r["retrieval_metrics"]["context_annotated_lines"]["recall"] for r in canonical)}
    return summary


def verify_response_evidence(case, response, trace, repository_root):
    require(len(trace["retrieval"]) == 1 and len(trace["contexts"]) == 1, "Expected one real retrieval/context per case")
    retrieval, data = trace["retrieval"][0], trace["contexts"][0]
    require(retrieval["repository"] == case["repository"] and retrieval["query"] == case["query"] and retrieval["top_k"] == 5, "Retrieval identity mismatch")
    retrieved = [RetrievalResult.model_validate(r, strict=True) for r in retrieval["results"]]
    built = ContextBuilder(max_context_chunks=data["max_context_chunks"], max_context_characters=data["max_context_characters"]).build(case["repository"], retrieved)
    require(json.loads(json.dumps(asdict(built))) == data, "Context replay mismatch")
    for item in retrieved:
        text = source_file(repository_root, item.path).read_bytes().decode("utf-8-sig")
        require(item.content == "".join(text.splitlines(keepends=True)[item.start_line - 1:item.end_line]), "Stale returned source")
    if response["http_status"] != 200:
        return {"metadata_valid": None, "citation_count": 0, "applicable": False, "response_received": False,
                "reason": "No final response evidence; error remains in answer denominator"}
    raw = response["body"]
    model = CodeChatResponse if case["mode"] == "code_chat" else IssueAnalysisResponse
    parsed = model.model_validate(raw, strict=True)
    query_field = "question" if case["mode"] == "code_chat" else "issue"
    require(parsed.repository == case["repository"] and raw[query_field] == case["query"] and parsed.retrieval.top_k == 5, "Answer request mismatch")
    require(parsed.retrieval.retrieved_chunks == len(retrieved) and parsed.retrieval.used_sources == len(built.sources)
            and parsed.retrieval.context_characters == built.characters_used and parsed.retrieval.context_budget_characters == built.max_context_characters, "Answer diagnostics mismatch")
    citations = CitationValidator().validate(case["repository"], parsed.source_ids, context=built, retrieval_results=retrieved)
    final = raw["citations"] if case["mode"] == "code_chat" else raw["related_code"]
    require([c.model_dump() for c in citations] == final, "Citation replay mismatch")
    for c, preview in zip(citations, parsed.sources, strict=True):
        source = built.source_map[c.source_id]
        require(preview.model_dump() == {"source_id": source.source_id, "language": source.language, "content": source.content}, "Preview differs from emitted context")
    require(len(trace["generations"]) == int(parsed.generation.called), "Generation count mismatch")
    if not built.sources:
        require(not parsed.generation.called and parsed.model is None and not parsed.source_ids, "Empty context must skip model")
    return {"metadata_valid": True if citations else None, "citation_count": len(citations), "applicable": bool(citations),
            "response_received": True, "empty_arrays_consistent": not citations,
            "reason": "Coordinates/previews match current context and local files; semantic support requires separate review"}


class Recorder:
    """Observation hooks in this CLI process only; no fabricated inference or provider settings."""
    def __init__(self):
        self.active = None
        self.enabled = False
        self.provider = None
        self.originals = {}

    async def network_request(self, request):
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            self.active["network_calls"].append({"host": request.url.host, "path": request.url.path,
                "model": body["model"], "messages": body["messages"], "request_fields": sorted(body),
                "schema": body["response_format"]["json_schema"]["schema"],
                "temperature": body.get("temperature"), "max_tokens": body.get("max_tokens"),
                "tools_advertised": "tools" in body or "tool_choice" in body})

    async def network_response(self, response):
        if response.request.url.path.endswith("/chat/completions"):
            await response.aread()
            choices = response.json().get("choices", [])
            self.active["network_calls"][-1]["reply"] = {"http_status": response.status_code,
                "content": choices[0].get("message", {}).get("content") if choices else None,
                "finish_reason": choices[0].get("finish_reason") if choices else None}

    def resolve(self):
        if self.provider is None:
            self.provider = get_llm_provider()
            client = self.provider._client._client
            client.event_hooks.setdefault("request", []).append(self.network_request)
            client.event_hooks.setdefault("response", []).append(self.network_response)
        recorder = self
        class ObservedProvider:
            @property
            def model_name(self): return recorder.provider.model_name
            async def complete_structured(self, messages, model):
                entry = {"model": self.model_name, "messages": [m.model_dump() for m in messages], "schema": model.model_json_schema()}
                recorder.active["generations"].append(entry)
                try:
                    result = await recorder.provider.complete_structured(messages, model)
                    entry["result"] = result.model_dump(mode="json")
                    return result
                except Exception as error:
                    entry["error_type"] = type(error).__name__
                    raise
        return ObservedProvider()

    def install(self):
        recorder = self
        self.originals = {"search": RetrievalService.search, "build": ContextBuilder.build, "validate": CitationValidator.validate,
                          "chat_resolve": code_chat.get_llm_provider, "issue_resolve": issues.get_llm_provider,
                          "chat_builder": code_chat.create_context_builder, "issue_builder": issues.create_context_builder}
        async def search(service, request):
            result = await recorder.originals["search"](service, request)
            if recorder.enabled:
                recorder.active["retrieval"].append(result.model_dump(mode="json"))
            return result
        def build(builder, repository, results):
            result = recorder.originals["build"](builder, repository, results)
            if recorder.enabled:
                recorder.active["contexts"].append(json.loads(json.dumps(asdict(result))))
            return result
        def validate(validator, repository, ids, **kwargs):
            result = recorder.originals["validate"](validator, repository, ids, **kwargs)
            if recorder.enabled:
                recorder.active["validations"].append({"repository": repository, "source_ids": list(ids), "citations": [c.model_dump() for c in result]})
            return result
        RetrievalService.search, ContextBuilder.build, CitationValidator.validate = search, build, validate
        code_chat.get_llm_provider = issues.get_llm_provider = self.resolve

    def select(self, case):
        self.enabled = True
        self.active = {"retrieval": [], "contexts": [], "generations": [], "network_calls": [], "validations": []}
        code_chat.create_context_builder = self.originals["chat_builder"]
        issues.create_context_builder = self.originals["issue_builder"]
        if case["context_budget_characters"] is not None:
            def budget_builder():
                normal = self.originals["chat_builder"]()
                return ContextBuilder(max_context_chunks=normal.max_context_chunks, max_context_characters=case["context_budget_characters"])
            code_chat.create_context_builder = issues.create_context_builder = budget_builder

    async def restore(self):
        RetrievalService.search = self.originals["search"]
        ContextBuilder.build = self.originals["build"]
        CitationValidator.validate = self.originals["validate"]
        code_chat.get_llm_provider, issues.get_llm_provider = self.originals["chat_resolve"], self.originals["issue_resolve"]
        code_chat.create_context_builder, issues.create_context_builder = self.originals["chat_builder"], self.originals["issue_builder"]
        if self.provider is not None:
            self.provider._client._client.event_hooks["request"].remove(self.network_request)
            self.provider._client._client.event_hooks["response"].remove(self.network_response)
            await self.provider._client.close()
            get_llm_provider.cache_clear()


async def evaluate(dataset_path, receipts, progress=print):
    data, digest, base = load_grounding_dataset(dataset_path, ROOT / "workspaces")
    settings = get_settings()
    client = create_qdrant_client(settings)
    recorder = Recorder()
    try:
        names = sorted(c.name for c in (await client.get_collections()).collections)
        before = await snapshot(client, names)
        recorder.install()
        rows = []
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://evaluation.local", timeout=180) as transport:
                for index, case in enumerate(data["cases"], 1):
                    recorder.select(case)
                    query_field, endpoint = ("question", "/api/code-chat") if case["mode"] == "code_chat" else ("issue", "/api/issue-analysis")
                    request = {"repository": case["repository"], query_field: case["query"], "top_k": 5}
                    reply = await transport.post(endpoint, json=request)
                    recorder.enabled = False
                    response = {"http_status": reply.status_code, "body": reply.json()}
                    trace = recorder.active
                    row = {"id": case["id"], "mode": case["mode"], "group": case["group"], "request": request,
                           "endpoint": endpoint, "response": response, "trace": trace,
                           "citation_check": verify_response_evidence(case, response, trace, base.repository_root)}
                    row["retrieval_metrics"] = measure_retrieval(case, trace["retrieval"][0]["results"], trace["contexts"][0])
                    # Every failure is retained; no retry or removal from the denominator.
                    with (receipts / f"{index:02}-{case['id']}.json").open("x", encoding="utf-8") as stream:
                        stream.write(json.dumps(row, ensure_ascii=False, indent=2) + "\n")
                    rows.append(row)
                    progress(f"{index}/{len(data['cases'])} {case['id']}: HTTP {reply.status_code}, citations={row['citation_check']['citation_count']}, native_calls={len(trace['network_calls'])}", flush=True)
        finally:
            await recorder.restore()
        verify_source_hashes(base)
        require(hashlib.sha256(dataset_path.read_bytes()).hexdigest() == digest, "Stage 3 labels changed during run")
        after_names = sorted(c.name for c in (await client.get_collections()).collections)
        require(names == after_names and before == await snapshot(client, after_names), "Persistent Qdrant changed during evaluation")
        return {"schema_version": 1, "kind": "real FastAPI ASGI evaluation; actual embedding and LLM HTTP, no mocked inference",
                "created_at": datetime.now(timezone.utc).isoformat(), "dataset": dataset_path.relative_to(ROOT).as_posix(),
                "dataset_sha256": digest, "source_sha256": data["source_sha256"],
                "configuration": {"collection": settings.qdrant_collection, "embedding_model": settings.embedding_model,
                                  "llm_model": settings.llm_model, "max_context_chunks": settings.max_context_chunks,
                                  "max_context_characters": settings.max_context_characters, "top_k": 5},
                "query_count": len(rows), "native_generation_http_calls": sum(len(r["trace"]["network_calls"]) for r in rows),
                "retrieval_summary": summarize_retrieval(rows), "cases": rows,
                "qdrant_before": before, "qdrant_unchanged": True,
                "manual_review": {"status": "pending", "answer_supported": "not inferred from HTTP status or valid metadata",
                                  "reviewer_requirement": "Inspect exact emitted Context, generated response, citations and fixed rubric. Record reviewer identity; assistant review is not independent human approval."},
                "budget_case_note": "Only explicitly labelled supplementary case sets 700 characters inside this isolated evaluator. Production .env/API settings are unchanged."}
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/datasets/stage3_grounding.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = args.output or ROOT / "evaluation/reports" / f"stage3_grounding_{stamp}.json"
    receipts = output.parent / f"{output.stem}_cases"
    require(not output.exists() and not receipts.exists(), "Existing report/receipt directory must not be overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    receipts.mkdir()
    report = asyncio.run(evaluate(args.dataset.resolve(), receipts))
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(output), "queries": report["query_count"], "native_generation_http_calls": report["native_generation_http_calls"],
                      "qdrant_unchanged": report["qdrant_unchanged"], "manual_review": "pending"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
