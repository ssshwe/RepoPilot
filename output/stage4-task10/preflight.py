"""Capture the unmodified Task 10 inputs before development or model execution."""
import asyncio
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT)]
from app.core.config import get_settings
from app.retrieval.vector_store import create_qdrant_client
from evaluation.stage4.state import write_new, read_json, full_snapshot, safe_configuration


async def main():
    output = ROOT / "output/stage4-task10"
    names = set(subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")) - {""}
    names.update(p.relative_to(ROOT).as_posix() for p in ROOT.rglob(".env") if ".venv" not in p.parts and "node_modules" not in p.parts and ".tools" not in p.parts)
    for name, value in {
        "workspace-before.json": {n: hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in sorted(names)},
        "settings-before.json": safe_configuration(get_settings()),
        "git-before.json": {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(), "status": subprocess.check_output(["git", "status", "--short"], cwd=ROOT).decode()},
    }.items():
        if (output/name).exists():
            old = read_json(output/name)
            assert old == value or name == "git-before.json", f"Preflight drift: {name}"
        else:
            write_new(output/name, value)
    client = create_qdrant_client(get_settings())
    try:
        write_new(output / "qdrant-before.json", await full_snapshot(client))
    finally:
        await client.close()
    print(f"Protected {len(names)} existing files and all Qdrant Collections")


if __name__ == "__main__":
    asyncio.run(main())
