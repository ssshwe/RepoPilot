"""Freeze existing Tasks 1–11 and evaluation artifacts; read-only Qdrant snapshot."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("task11_snapshot", ROOT / "output/stage3-task11/baseline.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
snapshot = module.snapshot


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    names = set(json.loads((ROOT / "output/stage3-task11/workspace-before.json").read_bytes()))
    for directory in ("services/api/app", "services/api/tests", "services/api/scripts", "apps/web/src",
                      "apps/web/tests", "docs", "evaluation", "output/stage3-task11", "workspaces"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                names.add(path.relative_to(ROOT).as_posix())
    save("workspace-before.json", {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in sorted(names)})
    current = snapshot()
    assert current == json.loads((ROOT / "output/stage3-task11/qdrant-after.json").read_bytes())
    save("qdrant-before.json", current)
    print(json.dumps({"protected_files": len(names), "qdrant_matches_task11": True,
                      "collections": [{"name": c["name"], "points": c["points_count"]} for c in current["collections"]]}))


if __name__ == "__main__":
    main()
