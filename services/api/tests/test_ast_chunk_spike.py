import json
import logging

from app.repository.scanner import RepositoryScanner
from scripts import ast_chunk_spike


def invoke(monkeypatch, root, *options):
    monkeypatch.setattr(ast_chunk_spike, "create_repository_scanner", lambda: RepositoryScanner(root))
    monkeypatch.setattr("sys.argv", ["ast_chunk_spike", "--repository", "demo", *options])
    return ast_chunk_spike.main()


def test_spike_reports_ast_fallback_and_failed_files_while_continuing(monkeypatch, tmp_path, capsys, caplog):
    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / "a.java").write_text("class A {}\n", encoding="utf-8")
    (repo / "b.vue").write_text("<template/>\n", encoding="utf-8")
    (repo / "c.java").write_text("class C { void xxxxxxxxxxxxxxxxxx() {} }\n", encoding="utf-8")
    (repo / "d.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        status = invoke(monkeypatch, tmp_path, "--max-characters", "30", "--preview-limit", "1")
    report = json.loads(capsys.readouterr().out)
    assert status == 1
    assert (report["ast_parsed_files"], report["fallback_files"], report["failed_files"], report["chunks_created"]) == (2, 1, 1, 3)
    assert report["files"][-1]["status"] == "ok" and report["files"][-1]["strategy"] == "ast"
    assert sum(len(file.get("chunks", [])) for file in report["files"]) == 1
    assert report["files"][2]["status"] == "failed" and "content" not in report["files"][2]
    assert "chunk_summary ast_parsed_files=2 fallback_files=1 failed_files=1 chunks=3" in caplog.text
    assert "xxxxxxxx" not in caplog.text


def test_spike_uses_scanner_approval_and_selected_path(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "demo"
    repo.mkdir()
    (repo / ".env").write_text("PRIVATE=value", encoding="utf-8")
    assert invoke(monkeypatch, tmp_path, "--path", ".env") == 1
    captured = capsys.readouterr()
    assert "scanner-approved" in captured.err and "PRIVATE" not in captured.err
    assert captured.out == ""
