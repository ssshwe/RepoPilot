"""Read-only index/configuration identities shared by baseline capture and replay."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))

from app.core.config import Settings
from evaluation.retrieval.evaluate import require


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_reference(path):
    path = Path(path).resolve()
    return {"path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def read_json(path):
    return json.loads(Path(path).read_bytes())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def safe_configuration(settings: Settings):
    # Explicit allowlist. Never serialize Settings, SecretStr, credentials or URLs.
    fields = (
        "llm_provider", "llm_model", "llm_timeout_seconds", "embedding_provider",
        "embedding_model", "embedding_timeout_seconds", "embedding_batch_size",
        "qdrant_collection", "qdrant_timeout_seconds", "repository_max_file_bytes",
        "repository_max_entries", "repository_max_total_bytes", "chunk_lines",
        "chunk_overlap_lines", "chunk_max_characters", "chunk_max_chunks",
        "indexing_max_text_characters", "indexing_max_vector_values",
        "max_context_chunks", "max_context_characters",
    )
    config = {field: getattr(settings, field) for field in fields}
    for prefix in ("llm", "embedding", "qdrant"):
        field = prefix + ("_url" if prefix == "qdrant" else "_base_url")
        config[field + "_sha256"] = hashlib.sha256(
            getattr(settings, field).strip().rstrip("/").encode()).hexdigest()
    return config


def runtime_identity():
    packages = ("fastapi", "pydantic", "qdrant-client", "openai", "tree-sitter",
                "tree-sitter-java", "tree-sitter-python", "tree-sitter-javascript",
                "tree-sitter-typescript")
    return {"python": sys.version, "packages": {p: importlib.metadata.version(p) for p in packages},
            "files": {name: file_reference(ROOT / name)["sha256"] for name in (
                "services/api/uv.lock", "services/api/pyproject.toml", "apps/web/package-lock.json",
                "services/api/app/retrieval/service.py", "services/api/app/retrieval/vector_store.py",
                "services/api/app/indexing/ast_chunker.py", "services/api/app/indexing/chunker.py",
                "services/api/app/context/builder.py")}}


async def full_snapshot(client):
    """Stable configuration plus complete sorted Records; no index writes."""
    state = {}
    names = sorted(c.name for c in (await client.get_collections()).collections)
    for name in names:
        info = await client.get_collection(name)
        points, offset = [], None
        while True:
            page, offset = await client.scroll(name, limit=100, offset=offset,
                                               with_payload=True, with_vectors=True)
            points.extend(point.model_dump(mode="json") for point in page)
            if offset is None:
                break
        state[name] = {"config": info.config.model_dump(mode="json"),
                       "points": sorted(points, key=lambda point: str(point["id"]))}
    require(names == sorted(c.name for c in (await client.get_collections()).collections),
            "Collection inventory changed during snapshot")
    return state


def legacy_snapshot(state):
    """Match the Task 12/13 evaluator's existing, smaller snapshot contract."""
    return {name: {"metadata": value["config"]["metadata"],
                   "vectors": value["config"]["params"]["vectors"],
                   "points_count": len(value["points"]),
                   "points_sha256": fingerprint(value["points"])}
            for name, value in state.items()}


def verify_unchanged(before, after, label):
    require(before == after, f"{label} changed; do not combine different baseline states")
