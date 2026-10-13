"""Real read-only Task 6 pipeline; write UTF-8 directly and prohibit generation."""
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "services/api"))

from app.core.config import get_settings
from scripts import context_spike

QUERY = "退款服务在哪里校验订单编号，然后把退款金额转回钱包？"


def main():
    settings = get_settings()
    assert settings.embedding_model == "qwen3-embedding:0.6b"
    assert urlsplit(settings.embedding_base_url).hostname in {"localhost", "127.0.0.1"}
    assert settings.qdrant_collection == "repopilot_code"
    previous = json.loads((OUT / "real-context-full.json").read_text(encoding="utf-8-sig"))
    (OUT / "transcoding-failure.json").write_text(json.dumps({
        "cause": "Native stdout decoded by a PowerShell pipeline before UTF-8 file writing",
        "query_roundtrip_failed": previous["request"]["query"] != QUERY,
        "replacement_characters_in_saved_query": previous["request"]["query"].count("\ufffd"),
        "resolution": "CLI --output writes UTF-8 directly; both real queries rerun below",
    }, indent=2) + "\n", encoding="utf-8")
    records = []
    with patch("app.llm.factory.ProviderFactory.create", side_effect=AssertionError("LLM forbidden in Task 6")) as llm:
        for name, budget in (("full", 16000), ("budget", 700)):
            target = OUT / f"real-context-{name}.json"
            status = context_spike.main([
                "--repository", "demo-repository", "--query", QUERY, "--top-k", "5",
                "--max-chunks", "3", "--max-characters", str(budget), "--output", str(target),
            ])
            assert status == 0
            result = json.loads(target.read_bytes().decode("utf-8"))
            assert result["request"]["query"] == QUERY
            assert result["llm_called"] is False
            assert result["context"]["characters_used"] == len(result["context"]["text"])
            records.append({"file": target.name, "budget": budget,
                            "source_count": len(result["context"]["sources"]),
                            "characters_used": result["context"]["characters_used"],
                            "query_utf8_roundtrip": True})
        llm.assert_not_called()
    evidence = {"runs": records, "llm_factory_calls": llm.call_count,
                "embedding_calls": "real configured local Ollama Provider; not mocked",
                "index_mutations": False}
    (OUT / "live-runs.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
