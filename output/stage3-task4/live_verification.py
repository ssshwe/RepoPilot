"""Task 4 evidence: two real HTTP re-index requests to the local demo index."""
import hashlib
import json
import math
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "services/api"))
from app.core.config import get_settings
from app.indexing.ast_chunker import create_ast_code_chunker
from app.indexing.service import prepare_repository
from app.repository.scanner import create_repository_scanner


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def snapshot(client):
    names = client.get("/collections")
    names.raise_for_status()
    collections = []
    for item in sorted(names.json()["result"]["collections"], key=lambda item: item["name"]):
        name = item["name"]
        response = client.get("/collections/" + name)
        response.raise_for_status()
        info = response.json()["result"]
        points, offset = [], None
        while True:
            body = dict(limit=100, with_payload=True, with_vector=True)
            if offset is not None:
                body["offset"] = offset
            response = client.post("/collections/" + name + "/points/scroll", json=body)
            response.raise_for_status()
            page = response.json()["result"]
            points.extend(page["points"])
            offset = page["next_page_offset"]
            if offset is None:
                break
        points.sort(key=lambda point: str(point["id"]))
        collections.append(dict(name=name, status=info["status"], points_count=len(points),
                                vectors=info["config"]["params"]["vectors"], metadata=info["config"].get("metadata"),
                                points_sha256=fingerprint(points), points=points))
    return dict(collections=collections)


def verify_index(snapshot_value, prepared, old):
    current = next(c for c in snapshot_value["collections"] if c["name"] == "repopilot_code")
    expected = {c.chunk_id: c for c in prepared.chunks}
    selected = [p for p in current["points"] if p["payload"].get("repository") == "demo-repository"]
    assert len(selected) == 17 and {str(p["id"]) for p in selected} == set(expected)
    old_target = next(c for c in old["collections"] if c["name"] == "repopilot_code")
    old_demo_ids = {str(p["id"]) for p in old_target["points"] if p["payload"].get("repository") == "demo-repository"}
    assert old_demo_ids.isdisjoint(expected)
    old_other = [p for p in old_target["points"] if p["payload"].get("repository") != "demo-repository"]
    new_other = [p for p in current["points"] if p["payload"].get("repository") != "demo-repository"]
    assert old_other == new_other
    for point in selected:
        chunk, payload = expected[str(point["id"])], point["payload"]
        for field in ("chunk_id", "repository", "path", "language", "content", "start_line", "end_line",
                      "symbol", "node_type", "parent_symbol", "chunking_strategy", "chunking_version"):
            assert payload[field] == getattr(chunk, field)
        source = next(f for f in prepared.scan.files if f.path == chunk.path)
        assert payload["content"] == "".join(source.content.splitlines(keepends=True)[chunk.start_line-1:chunk.end_line])
        assert len(point["vector"]) == 1024 and all(math.isfinite(v) for v in point["vector"])
        assert sum(v*v for v in point["vector"]) > 0
        assert payload["overlap_lines"] == (0 if chunk.chunking_strategy == "ast" else 10)
    assert current["metadata"]["repopilot_index"] == old_target["metadata"]["repopilot_index"]
    assert current["metadata"]["repopilot_chunking"]["demo-repository"] == prepared.chunking_metadata()
    for previous in old["collections"]:
        if previous["name"] != "repopilot_code":
            assert previous == next(c for c in snapshot_value["collections"] if c["name"] == previous["name"])
    return current


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    assert settings.qdrant_collection == "repopilot_code"
    assert urlsplit(settings.embedding_base_url).hostname in {"127.0.0.1", "localhost"}
    assert settings.embedding_model == "qwen3-embedding:0.6b"
    prepared = prepare_repository("demo-repository", create_repository_scanner(), create_ast_code_chunker(), settings.indexing_max_text_characters)
    assert len(prepared.chunks) == 17
    old = json.loads((OUT / "qdrant-before.json").read_text(encoding="utf-8"))
    headers = {}
    if settings.qdrant_api_key.get_secret_value():
        headers["api-key"] = settings.qdrant_api_key.get_secret_value()
    responses = []
    with httpx.Client(base_url="http://127.0.0.1:8014", timeout=180, trust_env=False) as api, httpx.Client(
        base_url=settings.qdrant_url, headers=headers, timeout=10, trust_env=False,
    ) as qdrant:
        assert api.get("/health").status_code == 200
        for name in ("first", "repeat"):
            response = api.post("/api/indexing/repositories/demo-repository")
            save(name + "-index-response.json", dict(http_status=response.status_code, response=response.json()))
            assert response.status_code == 200
            value = response.json()
            assert (value["files_scanned"], value["ast_parsed_files"], value["fallback_files"], value["failed_files"], value["chunks_created"], value["vectors_indexed"]) == (10, 4, 4, 0, 17, 17)
            responses.append(value)
            data = snapshot(qdrant)
            save("qdrant-" + name + ".json", data)
            current = verify_index(data, prepared, old)
        assert responses[0]["previous_vectors"] == 8 and responses[1]["previous_vectors"] == 17
        search = api.post("/api/retrieval/search", json={"repository": "demo-repository", "query": "退款逻辑在哪里？", "top_k": 5})
        save("dense-retrieval-smoke.json", dict(http_status=search.status_code, response=search.json()))
        assert search.status_code == 200 and len(search.json()["results"]) == 5
        assert all(hit["repository"] == "demo-repository" and hit["chunk_id"] in {c.chunk_id for c in prepared.chunks} for hit in search.json()["results"])
    summary = dict(date="2026-10-07", timezone="Asia/Shanghai", transport="real HTTP / uvicorn on 127.0.0.1:8014",
                   first=responses[0], repeat=responses[1], source_slices_verified=True,
                   stable_ids_verified=True, old_simple_ids_removed=True, other_collections_unchanged=True,
                   embedding_identity_preserved=True, vector_dimension=1024, all_vectors_finite=True,
                   final_points_sha256=current["points_sha256"], dense_retrieval_http_status=200,
                   dense_retrieval_results=5)
    save("live-verification.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
