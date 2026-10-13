from dataclasses import replace
import logging
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import ChunkingLimitError
from app.indexing.chunker import SimpleCodeChunker, create_code_chunker
from app.main import app
from app.repository.models import RepositoryFile
from app.repository.scanner import RepositoryScanner


def source(content: str, **overrides) -> RepositoryFile:
    values = dict(
        repository="demo", path="src/refund.py", language="python",
        content=content, size_bytes=len(content.encode("utf-8")),
        line_count=len(content.splitlines()),
    )
    return RepositoryFile(**(values | overrides))


@pytest.mark.parametrize("count,window,overlap,ranges", [
    (1, 3, 1, [(1, 1)]),
    (3, 3, 1, [(1, 3)]),
    (4, 3, 1, [(1, 3), (3, 4)]),
    (5, 3, 1, [(1, 3), (3, 5)]),
    (6, 3, 1, [(1, 3), (3, 5), (5, 6)]),
    (6, 3, 0, [(1, 3), (4, 6)]),
    (5, 3, 2, [(1, 3), (2, 4), (3, 5)]),
    (4, 1, 0, [(1, 1), (2, 2), (3, 3), (4, 4)]),
])
def test_inclusive_ranges_complete_coverage_and_exact_source_slices(count, window, overlap, ranges):
    file = source("".join(f"line {index}\n" for index in range(1, count + 1)))
    chunks = SimpleCodeChunker(chunk_lines=window, overlap_lines=overlap).chunk_file(file)
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == ranges
    lines = file.content.splitlines(keepends=True)
    covered = set()
    for chunk in chunks:
        assert chunk.content == "".join(lines[chunk.start_line - 1:chunk.end_line])
        assert len(chunk.content.splitlines()) == chunk.end_line - chunk.start_line + 1
        assert chunk.repository == file.repository
        assert chunk.path == file.path and chunk.language == file.language
        covered.update(range(chunk.start_line, chunk.end_line + 1))
    assert covered == set(range(1, count + 1))
    for previous, following in zip(chunks, chunks[1:]):
        assert previous.end_line - following.start_line + 1 == overlap
        if overlap:
            assert previous.content.splitlines(keepends=True)[-overlap:] == following.content.splitlines(keepends=True)[:overlap]


@pytest.mark.parametrize("content", [
    "a\nb\nc\nd", "a\nb\nc\nd\n", "a\r\nb\r\nc\r\nd\r\n",
    "a\rb\rc\rd", "第一行\n    退款()\n\t钱包()\n", "a\u2028b\u2029c\n",
])
def test_zero_overlap_reconstructs_original_text_and_preserves_line_endings(content):
    chunks = SimpleCodeChunker(chunk_lines=2, overlap_lines=0).chunk_file(source(content))
    assert "".join(chunk.content for chunk in chunks) == content


@pytest.mark.parametrize("content", ["", "\n", "\r\n\r\n", " \t\n \n", "\u3000\n"])
def test_empty_or_whitespace_only_files_create_no_chunks(content):
    assert SimpleCodeChunker().chunk_file(source(content)) == []


def test_blank_windows_are_skipped_without_trimming_or_renumbering_source_lines():
    file = source("\n \t\nalpha\n\n\nbeta\n")
    chunks = SimpleCodeChunker(chunk_lines=2, overlap_lines=0).chunk_file(file)
    assert [(chunk.start_line, chunk.end_line, chunk.content) for chunk in chunks] == [
        (3, 4, "alpha\n\n"), (5, 6, "\nbeta\n"),
    ]


def test_overlap_does_not_create_an_extra_suffix_only_chunk():
    chunks = SimpleCodeChunker(chunk_lines=4, overlap_lines=2).chunk_file(source("a\nb\nc\nd\ne\nf\n"))
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 4), (3, 6)]


def test_content_is_authoritative_for_ranges_not_a_declared_line_count():
    chunks = SimpleCodeChunker(chunk_lines=2, overlap_lines=0).chunk_file(source("a\nb\nc", line_count=999))
    assert [(chunk.start_line, chunk.end_line) for chunk in chunks] == [(1, 2), (3, 3)]


def test_ids_are_valid_uuids_stable_across_runs_and_sensitive_to_source_identity():
    file = source("refund()\ncredit()\n")
    first = SimpleCodeChunker().chunk_file(file)[0]
    repeated = SimpleCodeChunker().chunk_file(file)[0]
    assert first == repeated and UUID(first.chunk_id).version == 5
    for changed in [
        replace(file, repository="other"), replace(file, path="src/wallet.py"),
        replace(file, language="javascript"), replace(file, content="refund()\nother()\n"),
        replace(file, content="refund()\r\ncredit()\r\n"),
    ]:
        assert SimpleCodeChunker().chunk_file(changed)[0].chunk_id != first.chunk_id
    assert SimpleCodeChunker(chunk_lines=60, overlap_lines=0).chunk_file(file)[0].chunk_id != first.chunk_id


def test_repeated_code_at_different_ranges_has_distinct_ids():
    chunks = SimpleCodeChunker(chunk_lines=1, overlap_lines=0).chunk_file(source("same()\nsame()\n"))
    assert chunks[0].content == chunks[1].content
    assert chunks[0].chunk_id != chunks[1].chunk_id


@pytest.mark.parametrize("options", [
    dict(chunk_lines=0), dict(chunk_lines=201), dict(chunk_lines=True), dict(chunk_lines=1.5),
    dict(overlap_lines=-1), dict(chunk_lines=5, overlap_lines=5),
    dict(chunk_lines=5, overlap_lines=6), dict(overlap_lines=True),
    dict(max_file_bytes=0), dict(max_chunk_characters=0), dict(max_chunks=0),
])
def test_invalid_windows_or_limits_are_rejected_before_iteration(options):
    with pytest.raises(ValueError):
        SimpleCodeChunker(**options)


def test_file_byte_limit_is_enforced_even_with_incorrect_small_metadata():
    chunker = SimpleCodeChunker(max_file_bytes=4)
    assert len(chunker.chunk_file(source("abcd"))) == 1
    for file in [source("abcde"), source("abcde", size_bytes=1), source("ab", size_bytes=5), source("退款")]:
        with pytest.raises(ChunkingLimitError, match="file byte limit"):
            chunker.chunk_file(file)


def test_chunk_character_limit_handles_long_lines_and_is_not_a_utf8_byte_limit():
    chunker = SimpleCodeChunker(max_chunk_characters=4)
    assert chunker.chunk_file(source("退款金额"))[0].content == "退款金额"
    with pytest.raises(ChunkingLimitError, match="character limit"):
        chunker.chunk_file(source("12345"))
    with pytest.raises(ChunkingLimitError, match="character limit"):
        chunker.chunk_file(source("ab\ncd\n"))
    assert [chunk.content for chunk in SimpleCodeChunker(
        chunk_lines=1, overlap_lines=0, max_chunk_characters=4,
    ).chunk_file(source("ab\ncd\n"))] == ["ab\n", "cd\n"]


def test_file_chunk_count_limit_is_enforced():
    chunker = SimpleCodeChunker(chunk_lines=1, overlap_lines=0, max_chunks=2)
    assert len(chunker.chunk_file(source("a\nb\n"))) == 2
    with pytest.raises(ChunkingLimitError, match="chunk count limit"):
        chunker.chunk_file(source("a\nb\nc\n"))


@pytest.mark.parametrize("options", [
    dict(chunk_lines=0), dict(chunk_lines=201), dict(chunk_overlap_lines=-1),
    dict(chunk_overlap_lines=60), dict(chunk_max_characters=0), dict(chunk_max_characters=65537),
    dict(chunk_max_chunks=0), dict(chunk_max_chunks=20001),
])
def test_environment_settings_validate_windows_and_limits(options):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **options)


def test_factory_uses_settings_and_explicit_zero_overlap_override():
    settings = Settings(
        _env_file=None, chunk_lines=25, chunk_overlap_lines=5,
        chunk_max_characters=100, chunk_max_chunks=20, repository_max_file_bytes=200,
    )
    with patch("app.indexing.chunker.get_settings", return_value=settings):
        default = create_code_chunker()
        overridden = create_code_chunker(chunk_lines=3, overlap_lines=0)
    assert default.chunk_lines == 25 and default.overlap_lines == 5
    assert overridden.chunk_lines == 3 and overridden.overlap_lines == 0
    assert default.max_file_bytes == 200 and default.max_chunk_characters == 100
    assert default.max_chunks == 20


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspaces"
    repo = root / "demo"
    repo.mkdir(parents=True)
    return root, repo


def write_file(repo, name, content):
    (repo / name).write_bytes(content.encode("utf-8"))


def post_chunks(root, body, chunker=None):
    settings = Settings(_env_file=None)
    with patch("app.api.chunks.create_repository_scanner", return_value=RepositoryScanner(root)), patch(
        "app.indexing.chunker.get_settings", return_value=settings,
    ), TestClient(app) as http:
        if chunker is None:
            return http.post("/api/chunks/debug", json=body)
        with patch("app.api.chunks.create_code_chunker", return_value=chunker):
            return http.post("/api/chunks/debug", json=body)


def test_debug_counts_all_chunks_but_returns_only_bounded_preview(workspace):
    root, repo = workspace
    write_file(repo, "a.py", "a\nb\nc\nd\ne\nf\n")
    write_file(repo, "b.java", "class Refund {}\n")
    body = dict(repository="demo", chunk_lines=3, overlap_lines=1, preview_limit=1)
    response = post_chunks(root, body)
    assert response.status_code == 200
    result = response.json()
    assert result["files_scanned"] == result["supported_files"] == result["files_chunked"] == 2
    assert result["chunks_created"] == 4 and result["chunks_returned"] == 1 and result["truncated"]
    assert [file["chunks_created"] for file in result["files"]] == [3, 1]
    chunk = result["chunks"][0]
    assert set(chunk) == {"chunk_id", "repository", "path", "language", "start_line", "end_line", "content"}
    assert chunk["content"] == "a\nb\nc\n" and chunk["start_line"] == 1 and chunk["end_line"] == 3
    assert result["chunk_lines"] == 3 and result["overlap_lines"] == 1
    assert post_chunks(root, body).json() == result


def test_debug_lists_empty_files_and_scanner_skips_without_garbage_chunks(workspace):
    root, repo = workspace
    write_file(repo, "code.py", "refund()\n")
    write_file(repo, "empty.md", "")
    write_file(repo, "blank.py", " \t\n\n")
    write_file(repo, "notes.txt", "notes")
    write_file(repo, "package-lock.json", "{}")
    result = post_chunks(root, {"repository": "demo"}).json()
    assert result["files_scanned"] == 5 and result["supported_files"] == 3 and result["skipped_files"] == 2
    assert result["files_chunked"] == result["chunks_created"] == result["chunks_returned"] == 1
    assert result["files_without_chunks"] == ["blank.py", "empty.md"] and not result["truncated"]
    assert result["chunks"][0]["path"] == "code.py"


def test_debug_empty_repository_returns_zero_counts(workspace):
    root, _ = workspace
    response = post_chunks(root, {"repository": "demo"})
    assert response.status_code == 200
    result = response.json()
    assert result["files_scanned"] == result["chunks_created"] == result["chunks_returned"] == 0
    assert result["files"] == result["chunks"] == [] and not result["truncated"]


def test_whole_repository_chunk_budget_is_enforced_across_files(workspace):
    root, repo = workspace
    write_file(repo, "a.py", "a\n")
    write_file(repo, "b.py", "b\n")
    response = post_chunks(root, {"repository": "demo"}, SimpleCodeChunker(max_chunks=1))
    assert response.status_code == 413 and "chunk count limit" in response.json()["detail"]
    assert "chunks" not in response.json()


def test_preview_does_not_hide_an_oversized_later_chunk(workspace):
    root, repo = workspace
    write_file(repo, "a.py", "a\n")
    write_file(repo, "z.py", "12345678901")
    response = post_chunks(root, {"repository": "demo", "preview_limit": 1}, SimpleCodeChunker(max_chunk_characters=10))
    assert response.status_code == 413 and "character limit" in response.json()["detail"]
    assert "chunks" not in response.json()


def test_debug_oversized_source_is_skipped_by_scanner_before_chunking(workspace):
    root, repo = workspace
    write_file(repo, "a.py", "x" * 5)
    with patch("app.api.chunks.create_repository_scanner", return_value=RepositoryScanner(root, max_file_bytes=4)), TestClient(app) as http:
        response = http.post("/api/chunks/debug", json={"repository": "demo"})
    assert response.status_code == 200
    assert response.json()["chunks_created"] == 0
    assert response.json()["skipped"] == [{"path": "a.py", "reason": "too_large"}]


@pytest.mark.parametrize("body", [
    {}, {"repository": "../outside"}, {"repository": "demo", "path": "D:/outside"},
    {"repository": "demo", "chunk_lines": 0}, {"repository": "demo", "chunk_lines": 201},
    {"repository": "demo", "chunk_lines": True}, {"repository": "demo", "chunk_lines": 1.5},
    {"repository": "demo", "overlap_lines": -1}, {"repository": "demo", "overlap_lines": True},
    {"repository": "demo", "chunk_lines": 5, "overlap_lines": 5},
    {"repository": "demo", "preview_limit": 0}, {"repository": "demo", "preview_limit": 21},
    {"repository": "demo", "preview_limit": True},
])
def test_invalid_request_does_not_construct_scanner_or_chunker(body):
    with patch("app.api.chunks.create_repository_scanner") as scanner, patch(
        "app.api.chunks.create_code_chunker"
    ) as chunker, TestClient(app) as http:
        assert http.post("/api/chunks/debug", json=body).status_code == 422
    scanner.assert_not_called()
    chunker.assert_not_called()


def test_partial_override_conflicting_with_defaults_fails_before_disk_reads():
    with patch("app.indexing.chunker.get_settings", return_value=Settings(_env_file=None)), patch(
        "app.api.chunks.create_repository_scanner"
    ) as scanner, TestClient(app) as http:
        response = http.post("/api/chunks/debug", json={"repository": "demo", "chunk_lines": 5})
    assert response.status_code == 422 and "provide both options" in response.json()["detail"]
    scanner.assert_not_called()


def test_missing_repository_retains_scanner_404(workspace):
    root, _ = workspace
    response = post_chunks(root, {"repository": "missing"})
    assert response.status_code == 404 and str(root) not in response.text


def test_chunk_debug_does_not_call_embedding_or_qdrant_and_does_not_log_source(workspace, caplog):
    root, repo = workspace
    write_file(repo, "a.py", "DO_NOT_LOG_CODE\n")
    with patch("app.embedding.factory.EmbeddingProviderFactory.create") as embedding, patch(
        "app.retrieval.vector_store.create_qdrant_client"
    ) as qdrant, caplog.at_level(logging.INFO):
        response = post_chunks(root, {"repository": "demo"})
    assert response.status_code == 200 and "DO_NOT_LOG_CODE" in response.json()["chunks"][0]["content"]
    assert "DO_NOT_LOG_CODE" not in caplog.text and str(root) not in caplog.text
    embedding.assert_not_called()
    qdrant.assert_not_called()
