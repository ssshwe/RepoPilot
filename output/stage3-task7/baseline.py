"""Read-only Task 7 index snapshot and protected-file baseline."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "services/api"))
from app.core.config import get_settings


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot():
    spec = importlib.util.spec_from_file_location("task4_read_only_snapshot", ROOT / "output/stage3-task4/live_verification.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = get_settings()
    headers = {}
    if settings.qdrant_api_key.get_secret_value():
        headers["api-key"] = settings.qdrant_api_key.get_secret_value()
    with httpx.Client(base_url=settings.qdrant_url, headers=headers, trust_env=False, timeout=10) as client:
        return module.snapshot(client)  # Never run Task 4's indexing main().


def main():
    previous = json.loads((ROOT / "output/stage3-task6/workspace-before.json").read_text(encoding="utf-8"))
    names = set(previous)
    for directory in ("services/api/app", "services/api/scripts", "services/api/tests", "docs"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                names.add(path.relative_to(ROOT).as_posix())
    for number in range(1, 7):
        for path in (ROOT / f"output/stage3-task{number}").rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                names.add(path.relative_to(ROOT).as_posix())
    files = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}
    save("workspace-before.json", files)
    value = snapshot()
    assert value == json.loads((ROOT / "output/stage3-task6/qdrant-after.json").read_text(encoding="utf-8"))
    save("qdrant-before.json", value)
    print(json.dumps({"protected_files": len(files), "qdrant_matches_task6": True,
                      "collections": [{"name": c["name"], "points": c["points_count"]} for c in value["collections"]]}))


if __name__ == "__main__":
    main()
