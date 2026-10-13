"""Validate saved real browser receipts; no browser requests or fabricated model results."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))
from app.schemas.code_chat import CodeChatResponse
from app.schemas.issue_analysis import IssueAnalysisResponse
from app.schemas.retrieval import RetrievalSearchResponse
from evaluation.retrieval.evaluate import require
from evaluation.stage4.state import file_reference, read_json, write_new

OUT = ROOT / "output/playwright"


def receipt(filename):
    for line in (OUT / filename).read_text(encoding="utf-8").splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"No JSON receipt in {filename}")


retrieval = receipt("stage3-task13-retrieval-response.log")
chat = receipt("stage3-task13-code-chat-response.log")
issue = receipt("stage3-task13-issue-real.log")["body"]
RetrievalSearchResponse.model_validate(retrieval, strict=True)
CodeChatResponse.model_validate(chat, strict=True)
IssueAnalysisResponse.model_validate(issue, strict=True)
require(len(retrieval["results"]) == 5 and chat["generation"]["called"] and issue["generation"]["called"], "Real normal UI requests failed")
sources = receipt("stage3-task13-sources.log")
require(sources["passed"] and sources["visible_preview"], "Source collapse/expand failed")
empty, late = {}, {}
for name in ("retrieval", "code-chat", "issue"):
    value = receipt(f"stage3-task13-{name}-empty.log")
    require(value["passed"] and value["status"] == 200, "Real empty control failed")
    require(not value["body"]["results"] if name == "retrieval" else
            value["body"]["status"] == "insufficient_evidence" and not value["body"]["generation"]["called"]
            and not value["body"]["sources"], "Empty control evidence/generation mismatch")
    empty[name] = value
    suffix = "after-fix" if name == "retrieval" else "final" if name == "issue" else None
    value = receipt(f"stage3-task13-{name}-late{('-' + suffix) if suffix else ''}.log")
    require(value["passed"] and value["before"] == value["after"] and value["delivered_after_clear"]
            and value["response_content_mocked"] is False and value["real_response"]["status"] == 200, "Late delivery UI guard failed")
    late[name] = value
index = read_json(ROOT / "output/stage3-task13/qdrant-before.json")["repopilot_code"]["points"]
for body, key in ((chat, "citations"), (issue, "related_code")):
    for citation, preview in zip(body[key], body["sources"], strict=True):
        require(any(p["payload"]["path"] == citation["path"]
                    and p["payload"]["start_line"] == citation["start_line"]
                    and p["payload"]["end_line"] == citation["end_line"]
                    and p["payload"]["symbol"] == citation["symbol"]
                    and p["payload"]["content"] == preview["content"] for p in index), "UI preview differs from frozen source")
checks = {"real_retrieval": True, "real_code_chat": True, "real_issue": True, "sources_expand_collapse": True,
          **{f"{name}_empty": True for name in empty}, **{f"{name}_late_delivery_guard": True for name in late},
          "preview_coordinates": True, "desktop_screenshots_visually_checked": True}
filenames = [f"stage3-task13-{name}.png" for name in ("retrieval", "code-chat", "issue")]
filenames += ["stage3-task13-retrieval-response.log", "stage3-task13-code-chat-response.log", "stage3-task13-issue-real.log",
              "stage3-task13-sources.log", "stage3-task13-retrieval-late.log", "stage3-task13-late-response.js"]
filenames += [f"stage3-task13-{name}-empty.log" for name in empty]
filenames += ["stage3-task13-retrieval-late-after-fix.log", "stage3-task13-code-chat-late.log", "stage3-task13-issue-late-final.log"]
write_new(OUT / "stage3-task13-verification.json", {"run_date": "2026-10-13", "passed": True, "checks": checks,
    "real_responses": {"retrieval": retrieval, "code_chat": chat, "issue": issue}, "empty_controls": empty,
    "controlled_late_responses": late, "artifacts": {name: file_reference(OUT / name) for name in filenames},
    "reviewer": "development assistant; actual screenshots and browser UI snapshots inspected",
    "regression_fixed": "Retrieval Debug late delivery after cancellation; failed first log retained",
    "generation_quality_note": "Normal Issue reply copied Context markers into suggestions; this is a separately saved failure, outside the fixed 16-case denominator",
    "timing_note": "Late-response controls delay real API response delivery and ignore AbortSignal; they do not assert server inference cancellation"})
print("Browser receipts verified: all three workspaces, empty controls, actual preview coordinates, late delivery guards")
