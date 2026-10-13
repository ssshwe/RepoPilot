"""Protect Task 1–8 files and snapshot all existing Qdrant collections read-only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("task8_read_only_snapshot", ROOT / "output/stage3-task8/baseline.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
snapshot = module.snapshot


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    names = set(json.loads((ROOT / "output/stage3-task8/workspace-before.json").read_bytes()))
    for directory in ("services/api/app", "services/api/scripts", "services/api/tests", "docs", "output/stage3-task8", "workspaces"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                names.add(path.relative_to(ROOT).as_posix())
    save("workspace-before.json", {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)})
    before = snapshot()
    assert before == json.loads((ROOT / "output/stage3-task8/qdrant-after.json").read_bytes())
    save("qdrant-before.json", before)
    print(json.dumps({"protected_files": len(names), "qdrant_matches_task8": True,
                      "collections": [{"name": c["name"], "points": c["points_count"]} for c in before["collections"]]}))


if __name__ == "__main__":
    main()
