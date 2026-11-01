"""Reuse the protected publication scanner and verify current Task 11 links."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import runpy

ROOT = Path(__file__).resolve().parents[2]
buffer = io.StringIO()
with redirect_stdout(buffer):
    try:
        runpy.run_path(str(ROOT/"output/stage4-task10/public_scan.py"),run_name="__main__")
    except SystemExit:
        pass
result = json.loads(buffer.getvalue())
for name in ("README.md","evaluation/README.md","docs/stage-4-task-11-expansion.md"):
    path = ROOT/name
    for target in re.findall(r"\]\(([^)]+)\)",path.read_text(encoding="utf-8")):
        if "://" in target or target.startswith("#"):
            continue
        target = target.split("#")[0]
        if target and not (path.parent/target).exists():
            result["broken_local_links"].append({"document":name,"target":target})
result["passed"] = result["passed"] and not result["broken_local_links"]
print(json.dumps(result,ensure_ascii=False,indent=2))
if not result["passed"]:
    raise SystemExit(1)
