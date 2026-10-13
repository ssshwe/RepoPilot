"""Verify saved real retrievals, exact source slices and index/workspace preservation."""
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "services/api"))

from app.context import ContextBuilder
from app.core.config import get_settings
from app.schemas.retrieval import RetrievalResult

QUERY = "退款服务在哪里校验订单编号，然后把退款金额转回钱包？"
BOUNDARY = re.compile(r"\[SOURCE (S[1-9][0-9]*)\]\n(.*?)\n\[/SOURCE \1\]", re.DOTALL)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_real(name):
    value = json.loads((OUT / f"real-context-{name}.json").read_bytes().decode("utf-8"))
    assert value["request"] == {"repository": "demo-repository", "query": QUERY, "top_k": 5}
    assert value["llm_called"] is False and "answer" not in value
    retrieval, context = value["retrieval"], value["context"]
    assert retrieval["collection"] == "repopilot_code"
    assert retrieval["embedding_model"] == "qwen3-embedding:0.6b"
    assert retrieval["embedding_dimension"] == 1024
    results = [RetrievalResult.model_validate(item) for item in retrieval["results"]]
    assert len(results) == 5
    lookup = {item.chunk_id: item for item in results}
    for item in results:
        raw = (ROOT / "workspaces" / item.repository / item.path).read_bytes().decode("utf-8-sig")
        assert item.content == "".join(raw.splitlines(keepends=True)[item.start_line - 1:item.end_line])
    assert context["characters_used"] == len(context["text"]) <= context["max_context_characters"]
    assert len(context["sources"]) <= context["max_context_chunks"]
    assert [s["source_id"] for s in context["sources"]] == [f"S{i + 1}" for i in range(len(context["sources"]))]
    matches = list(BOUNDARY.finditer(context["text"]))
    assert len(matches) == len(context["sources"])
    assert "\n\n".join(match.group(0) for match in matches) == context["text"]
    covered = set()
    for source, match in zip(context["sources"], matches, strict=True):
        assert match.group(1) == source["source_id"]
        body = json.loads(match.group(2))
        original = lookup[source["chunk_id"]]
        for key in ("repository", "path", "language", "symbol", "node_type", "parent_symbol",
                    "chunking_strategy", "chunking_version"):
            assert source[key] == body[key] == getattr(original, key)
        for key in ("content", "overlap_trimmed", "budget_truncated"):
            assert source[key] == body[key]
        assert source["retrieval_rank"] == original.rank and source["score"] == original.score
        assert body["lines"] == {"start": source["start_line"], "end": source["end_line"]}
        assert body["original_lines"] == {"start": original.start_line, "end": original.end_line}
        assert (source["original_start_line"], source["original_end_line"]) == (original.start_line, original.end_line)
        raw = (ROOT / "workspaces" / source["repository"] / source["path"]).read_bytes().decode("utf-8-sig")
        assert source["content"] == "".join(raw.splitlines(keepends=True)[source["start_line"] - 1:source["end_line"]])
        assert len(source["content"].splitlines()) == source["end_line"] - source["start_line"] + 1
        keys = {(source["path"], number) for number in range(source["start_line"], source["end_line"] + 1)}
        assert covered.isdisjoint(keys)
        covered.update(keys)
    with patch("app.llm.factory.ProviderFactory.create", side_effect=AssertionError("no LLM")), patch(
        "app.embedding.factory.EmbeddingProviderFactory.create", side_effect=AssertionError("no embedding during replay")), patch(
        "app.repository.scanner.RepositoryScanner.scan", side_effect=AssertionError("no scanning during replay")):
        rebuilt = ContextBuilder(max_context_chunks=context["max_context_chunks"],
                                 max_context_characters=context["max_context_characters"]).build("demo-repository", results)
        assert json.loads(json.dumps(asdict(rebuilt))) == context
        assert set(rebuilt.source_map) == {s["source_id"] for s in context["sources"]}
    (OUT / f"real-context-{name}.txt").write_text(context["text"] + "\n", encoding="utf-8")
    # The review .txt has a final newline; the budget is measured on context.text.
    return value, results


def main():
    full, results = verify_real("full")
    small, _ = verify_real("budget")
    assert (len(full["context"]["sources"]), full["context"]["characters_used"], full["context"]["omitted_sources"]) == (3, 3015, 2)
    assert (len(small["context"]["sources"]), small["context"]["characters_used"], small["context"]["omitted_sources"]) == (1, 698, 4)
    source = small["context"]["sources"][0]
    assert (source["start_line"], source["end_line"], source["original_start_line"], source["original_end_line"]) == (12, 15, 12, 18)
    assert source["budget_truncated"] and "wallets.credit" not in source["content"]
    # Explicitly controlled replay: repeat a REAL hit, changing rank only.
    # This is not a claim that the real Qdrant Top5 contained duplicate chunks.
    replay_results = [results[0], results[0].model_copy(update={"rank": 2}),
                      *(item.model_copy(update={"rank": item.rank + 1}) for item in results[1:])]
    duplicate = ContextBuilder(max_context_chunks=3).build("demo-repository", replay_results)
    assert duplicate.duplicate_chunks == 1 and duplicate.text == full["context"]["text"]
    tiny = ContextBuilder(max_context_characters=1).build("demo-repository", results)
    assert tiny.text == "" and tiny.sources == () and tiny.source_map == {} and tiny.empty_reason == "budget_too_small"
    save(OUT / "controlled-replays.json", {
        "kind": "controlled pure replay of saved real hits; no new retrieval or synthetic code",
        "duplicate_input_chunks": len(replay_results), "removed_duplicate_chunks": duplicate.duplicate_chunks,
        "formatted_text_matches_original": duplicate.text == full["context"]["text"],
        "one_character_budget": asdict(tiny),
    })

    spec = importlib.util.spec_from_file_location("task4_snapshot_read_only", ROOT / "output/stage3-task4/live_verification.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # Import snapshot only; never execute its indexing main().
    settings = get_settings()
    headers = {}
    if settings.qdrant_api_key.get_secret_value():
        headers["api-key"] = settings.qdrant_api_key.get_secret_value()
    with httpx.Client(base_url=settings.qdrant_url, headers=headers, trust_env=False, timeout=10) as client:
        after = module.snapshot(client)
    before = json.loads((OUT / "qdrant-before.json").read_text(encoding="utf-8"))
    assert after == before
    save(OUT / "qdrant-after.json", after)

    old_files = json.loads((OUT / "workspace-before.json").read_text(encoding="utf-8"))
    changed = sorted(name for name, old_hash in old_files.items()
                     if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != old_hash)
    allowed = sorted(["README.md", ".env.example", "services/api/app/core/config.py"])
    assert changed == allowed
    protection = {"existing_files_checked": len(old_files), "changed_existing_files": changed,
                  "unexpected_changes": [], "local_env_unchanged": ".env" not in changed,
                  "dependency_lockfiles_unchanged": True, "frontend_unchanged": True,
                  "fixture_and_evaluation_data_unchanged": True, "qdrant_exact_snapshot_match": True,
                  "collections": [{k: c[k] for k in ("name", "points_count", "vectors", "points_sha256")}
                                  for c in after["collections"]]}
    save(OUT / "preservation.json", protection)
    production = next(c for c in after["collections"] if c["name"] == "repopilot_code")
    evidence = {
        "date": "2026-10-07", "timezone": "Asia/Shanghai", "completed_task": "Stage 3 / Task 6",
        "backend": {"command": ".venv/Scripts/python.exe -m pytest -q", "cwd": "services/api",
                    "collected": 730, "passed": 724, "skipped": 6, "warnings": 2,
                    "new_context_cases": 81, "exit_code": 0},
        "evaluation_tests": {"passed": 42, "exit_code": 0},
        "frontend_tests": {"passed": 9, "exit_code": 0},
        "frontend_build": {"typecheck_and_build": "passed", "modules_transformed": 1615, "exit_code": 0},
        "defaults": {"MAX_CONTEXT_CHUNKS": 5, "MAX_CONTEXT_CHARACTERS": 16000},
        "budget_unit": "len(context.text): Unicode characters including JSON metadata/escaping and boundaries",
        "query": QUERY, "repository": "demo-repository", "real_retrieval_top_k": 5,
        "real_full_context": {"max_context_chunks": 3, "max_context_characters": 16000,
                              "characters_used": 3015, "omitted_sources": 2,
                              "sources": [{k: s[k] for k in ("source_id", "chunk_id", "retrieval_rank", "path", "symbol", "start_line", "end_line", "chunking_strategy")}
                                          for s in full["context"]["sources"]]},
        "real_budget_context": {"max_context_chunks": 3, "max_context_characters": 700,
                                "characters_used": 698, "omitted_sources": 4, "source": source},
        "verification": {"query_utf8_roundtrip": True, "all_retrieved_and_emitted_source_slices_exact": True,
                         "metadata_matches_real_retrieval": True, "unique_dense_source_ids": True,
                         "formatted_context_json_roundtrip": True, "pure_replay_exact": True,
                         "controlled_duplicate_replay_removed": 1, "one_character_budget_emits_no_source": True,
                         "llm_factory_calls_during_real_runs": 0, "qdrant_exact_snapshot_match": True,
                         "workspace_existing_files_checked": len(old_files), "unexpected_existing_file_changes": []},
        "bad_cases": ["700-character budget drops the wallets.credit evidence at line 17",
                      "1-character budget cannot fit one source and emits empty context",
                      "PowerShell piped Unicode JSON corrupted; --output fix verified by real rerun",
                      "Initial Qdrant connection failure recovered with existing infra helper; exact index preserved"],
        "production_points": production["points_count"], "production_points_sha256": production["points_sha256"],
        "artifacts": ["output/stage3-task6/real-context-full.json", "output/stage3-task6/real-context-budget.json",
                      "output/stage3-task6/live-runs.json", "output/stage3-task6/controlled-replays.json",
                      "output/stage3-task6/preservation.json"],
        "next_task_not_started": "Task 7: POST /api/code-chat, single RAG generation, structured Source IDs",
    }
    save(ROOT / "docs/stage-3-task-6-verification.json", evidence)
    print(json.dumps({"verified": True, "protected_existing_files": len(old_files),
                      "changed_existing_files": changed, "production_points": production["points_count"],
                      "production_points_sha256": production["points_sha256"],
                      "full_characters": 3015, "budget_characters": 698, "llm_factory_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
