"""Capture protected Task 11 inputs before development or model calls."""
import asyncio
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"services/api"), str(ROOT)]
from app.core.config import get_settings
from app.retrieval.vector_store import create_qdrant_client
from evaluation.stage4.state import write_new, read_json, full_snapshot, safe_configuration


async def main():
    output = ROOT/"output/stage4-task11"
    names = set(subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")) - {""}
    names.update(p.relative_to(ROOT).as_posix() for p in ROOT.rglob(".env") if not any(v in p.parts for v in (".venv", "node_modules", ".tools")))
    for name, value in {
        "workspace-before.json": {n: hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in sorted(names)},
        "settings-before.json": safe_configuration(get_settings()),
        "git-before.json": {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(), "status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT).decode()},
    }.items():
        if (output/name).exists():
            assert read_json(output/name) == value or name == "git-before.json", "Preflight drift: "+name
        else:
            write_new(output/name, value)
    client = create_qdrant_client(get_settings())
    try:
        snapshot = await full_snapshot(client)
        assert snapshot == read_json(ROOT/"output/stage4-task10/qdrant-after.json"), "Existing Qdrant differs from Task 10"
        write_new(output/"qdrant-before.json", snapshot)
    finally:
        await client.close()
    print(f"Protected {len(names)} files and all existing Collections")


if __name__ == "__main__":
    asyncio.run(main())
