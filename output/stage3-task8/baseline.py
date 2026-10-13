"""Protect previous work and snapshot the existing index without mutations."""
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
        return module.snapshot(client)


def main():
    previous = json.loads((ROOT / "output/stage3-task7/workspace-before.json").read_bytes())
    names = set(previous)
    for directory in ("services/api/app", "services/api/scripts", "services/api/tests", "docs", "output/stage3-task7"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                names.add(path.relative_to(ROOT).as_posix())
    save("workspace-before.json", {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)})
    value = snapshot()
    assert value == json.loads((ROOT / "output/stage3-task7/qdrant-after.json").read_bytes())
    save("qdrant-before.json", value)
    print(json.dumps({"protected_files": len(names), "qdrant_matches_task7": True,
                      "collections": [{"name": c["name"], "points": c["points_count"]} for c in value["collections"]]}))


if __name__ == "__main__":
    main()
