"""Check publication candidates without printing credentials or environment values."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
names = set(subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT).decode().split("\0")) - {""}
blocked, secrets, links = [], [], []
values = []
for env in ROOT.rglob(".env"):
    if any(p in {".venv", ".tools", "node_modules"} for p in env.parts):
        continue
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        if re.search(r"KEY|SECRET|TOKEN|PASSWORD", key) and len(value) > 8 and not re.search(r"placeholder|dummy|example|your-|replace|not-needed|local-|test-", value, re.I):
            values.append(value.encode())
tokens = re.compile(rb"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,})")
for name in sorted(names):
    path = ROOT/name
    if not path.is_file():
        continue
    if (path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example")
            or any(p in {".tools", ".venv", "node_modules", ".playwright-cli"} for p in path.parts)
            or name.endswith((".sqlite3-wal", ".sqlite3-shm"))):
        blocked.append(name)
    data = path.read_bytes()
    if tokens.search(data) or any(v in data for v in values):
        secrets.append(name)
    for marker in re.finditer(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----", data):
        if b"TASK9_FAKE_VALUE" not in data[marker.end():marker.end()+50]:
            secrets.append(name)
for name in ("README.md", "evaluation/README.md", "docs/stage-4-task-10-reranker.md"):
    path = ROOT/name
    for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        if "://" in target or target.startswith("#"):
            continue
        target = target.split("#")[0]
        if target and not (path.parent/target).exists():
            links.append({"document": name, "target": target})
result = {"files_scanned": len(names), "actual_secret_values_checked": len(values), "blocked_paths": sorted(set(blocked)),
          "secret_matches": sorted(set(secrets)), "broken_local_links": links, "passed": not (blocked or secrets or links)}
print(json.dumps(result, ensure_ascii=False, indent=2))
if not result["passed"]:
    raise SystemExit(1)
