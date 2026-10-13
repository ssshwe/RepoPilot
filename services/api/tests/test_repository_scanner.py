import logging
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import (
    RepositoryNotFoundError, RepositoryPathError, RepositoryScanError, RepositoryScanLimitError,
)
from app.main import app
from app.repository.ignore import IGNORED_DIRECTORIES
from app.repository.language import detect_language
from app.repository.paths import (
    UnsafeRepositoryEntry, check_contained_path, is_link_or_reparse, read_bounded_file,
    validate_repository_name,
)
from app.repository.scanner import RepositoryScanner, create_repository_scanner


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspaces"
    repo = root / "demo"
    repo.mkdir(parents=True)
    return root, repo


def write_file(repo, path, content=b"print('hello')\n"):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def make_symlink(link: Path, target: Path, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if os.name == "nt" and directory:
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
            )
            assert result.returncode == 0, result.stderr
            return
        pytest.skip(f"File symbolic links unavailable: {type(exc).__name__}, winerror={getattr(exc, 'winerror', None)}")


@pytest.mark.parametrize("path,language", [
    ("a.py", "python"), ("src/A.JAVA", "java"), ("a.js", "javascript"),
    ("a.ts", "typescript"), ("a.vue", "vue"), ("a.json", "json"),
    ("a.yml", "yaml"), ("a.yaml", "yaml"), ("a.md", "markdown"),
    ("a.sql", "sql"), ("a.txt", None), ("README", None), ("a.py.exe", None),
])
def test_language_allowlist(path, language):
    assert detect_language(path) == language


@pytest.mark.parametrize("name", [
    "", ".", "..", "../outside", "demo/../outside", "demo/other", "demo\\other",
    "D:\\secret", "/tmp/secret", "\\\\server\\share", "demo:stream", "%2e%2e",
    "has space", "a" * 65, "CON", "con.txt", "AUX", "NUL", "COM1", "LPT9.json",
    "demo.", "demo\n", 123, None,
])
def test_repository_name_rejects_paths_and_windows_aliases(name):
    with pytest.raises(RepositoryPathError):
        validate_repository_name(name)


@pytest.mark.parametrize("name", ["demo", "demo-repository", "a_b.c", "A" * 64, "123"])
def test_portable_repository_names(name):
    assert validate_repository_name(name) == name


@pytest.mark.parametrize("directory", sorted(IGNORED_DIRECTORIES))
def test_ignored_directories_are_pruned_before_descendants_are_read(workspace, directory):
    root, repo = workspace
    write_file(repo, f"nested/{directory}/should-not-read.py")
    allowed = write_file(repo, "nested/source.py")
    with patch("app.repository.scanner.read_bounded_file", wraps=read_bounded_file) as reader:
        result = RepositoryScanner(root).scan("demo")
    assert [file.path for file in result.files] == ["nested/source.py"]
    assert result.files_scanned == 1 and result.skipped == []
    assert [(entry.path, entry.reason) for entry in result.pruned_directories] == [
        (f"nested/{directory}", "ignored_directory")
    ]
    assert [call.args[0] for call in reader.call_args_list] == [allowed]


def test_ignore_rules_match_case_and_filename_patterns(workspace):
    root, repo = workspace
    write_file(repo, "NODE_MODULES/leak.py")
    write_file(repo, ".env.production.json", b"DO_NOT_READ")
    write_file(repo, "package-lock.json", b"DO_NOT_READ")
    write_file(repo, "bundle.min.js", b"DO_NOT_READ")
    write_file(repo, "package.json", b'{"name":"demo"}\n')
    with patch("app.repository.scanner.read_bounded_file", wraps=read_bounded_file) as reader:
        result = RepositoryScanner(root).scan("demo")
    assert [file.path for file in result.files] == ["package.json"]
    assert result.files_scanned == 4
    assert {entry.reason for entry in result.skipped} == {"ignored_file"}
    assert reader.call_count == 1


def test_scan_preserves_text_bom_newlines_indentation_and_real_byte_counts(workspace):
    root, repo = workspace
    raw = b"\xef\xbb\xbf" + "# 订单退款\r\n    refund()\r\n\r\n".encode("utf-8")
    write_file(repo, "src/退款.py", raw)
    write_file(repo, "empty.md", b"")
    write_file(repo, "last-line.ts", b"export const amount = 1;")
    write_file(repo, "unsupported.txt", b"notes")
    first = RepositoryScanner(root).scan("demo")
    second = RepositoryScanner(root).scan("demo")
    assert first == second
    assert first.files_scanned == 4
    assert len(first.files) == 3 and len(first.skipped) == 1
    assert first.total_bytes == len(raw) + len(b"export const amount = 1;")
    by_path = {file.path: file for file in first.files}
    assert by_path["src/退款.py"].content == "# 订单退款\r\n    refund()\r\n\r\n"
    assert by_path["src/退款.py"].line_count == 3
    assert by_path["src/退款.py"].size_bytes == len(raw)
    assert by_path["empty.md"].content == "" and by_path["empty.md"].line_count == 0
    assert by_path["last-line.ts"].line_count == 1
    assert first.skipped[0].reason == "unsupported_extension"


@pytest.mark.parametrize("payload,reason", [
    (b"text\x00tail", "binary"), (b"\x01text", "binary"),
    (b"text\x7f", "binary"), (b"\xff\xfe\xff", "invalid_encoding"),
])
def test_binary_and_invalid_encoding_are_not_supported(workspace, payload, reason):
    root, repo = workspace
    write_file(repo, "fake.py", payload)
    result = RepositoryScanner(root).scan("demo")
    assert result.files == [] and result.files_scanned == 1
    assert result.skipped[0].reason == reason


def test_exact_file_size_limit_is_accepted_and_oversized_file_is_skipped_without_open(workspace):
    root, repo = workspace
    write_file(repo, "at-limit.py", b"abcd")
    write_file(repo, "too-large.py", b"abcde")
    result = RepositoryScanner(root, max_file_bytes=4).scan("demo")
    assert [file.path for file in result.files] == ["at-limit.py"]
    assert result.skipped[0].reason == "too_large"
    with patch("app.repository.paths.os.open") as opener:
        with pytest.raises(UnsafeRepositoryEntry, match="too_large"):
            read_bounded_file(repo / "too-large.py", repo, 4)
    opener.assert_not_called()


def test_scan_entry_budget_includes_directory_entries_without_silent_partial_result(workspace):
    root, repo = workspace
    write_file(repo, "nested/a.py")
    write_file(repo, "nested/b.py")
    assert len(RepositoryScanner(root, max_entries=3).scan("demo").files) == 2
    with pytest.raises(RepositoryScanLimitError, match="entry limit"):
        RepositoryScanner(root, max_entries=2).scan("demo")


def test_total_accepted_byte_budget_is_enforced(workspace):
    root, repo = workspace
    write_file(repo, "a.py", b"abc")
    write_file(repo, "b.py", b"def")
    assert RepositoryScanner(root, max_total_bytes=6).scan("demo").total_bytes == 6
    with pytest.raises(RepositoryScanLimitError, match="text byte limit"):
        RepositoryScanner(root, max_total_bytes=5).scan("demo")


def test_missing_repository_empty_repository_and_non_directory(workspace):
    root, repo = workspace
    result = RepositoryScanner(root).scan("demo")
    assert result.files_scanned == 0 and result.files == [] and result.total_bytes == 0
    (root / "a-file").write_text("text", encoding="utf-8")
    for name in ["missing", "a-file"]:
        with pytest.raises(RepositoryNotFoundError):
            RepositoryScanner(root).scan(name)


def test_containment_uses_path_components_not_similar_prefix(workspace):
    root, repo = workspace
    outside = root.parent / "workspaces-extra"
    outside.mkdir()
    target = write_file(outside, "secret.py", b"DO_NOT_READ")
    with pytest.raises(UnsafeRepositoryEntry, match="outside_repository"):
        check_contained_path(target, root)
    with patch("app.repository.paths.os.open") as opener:
        with pytest.raises(UnsafeRepositoryEntry):
            read_bounded_file(target, repo, 1024)
    opener.assert_not_called()


def test_file_link_is_never_read(workspace):
    root, repo = workspace
    outside = root.parent / "outside"
    outside.mkdir()
    secret = write_file(outside, "secret.py", b"DO_NOT_READ")
    make_symlink(repo / "linked.py", secret)
    write_file(repo, "safe.py")
    with patch("app.repository.scanner.read_bounded_file", wraps=read_bounded_file) as reader:
        result = RepositoryScanner(root).scan("demo")
    assert [file.path for file in result.files] == ["safe.py"]
    assert result.files_scanned == 2
    assert {entry.reason for entry in result.skipped} == {"unsafe_link"}
    assert reader.call_count == 1


def test_directory_link_and_cycle_are_pruned_without_reading_descendants(workspace):
    root, repo = workspace
    outside = root.parent / "outside"
    outside.mkdir()
    write_file(outside, "secret.py", b"DO_NOT_READ")
    make_symlink(repo / "linked-directory", outside, directory=True)
    make_symlink(repo / "cycle", repo, directory=True)
    write_file(repo, "safe.py")
    with patch("app.repository.scanner.read_bounded_file", wraps=read_bounded_file) as reader:
        result = RepositoryScanner(root).scan("demo")
    assert [file.path for file in result.files] == ["safe.py"]
    assert result.files_scanned == 3
    assert {entry.reason for entry in result.skipped} == {"unsafe_link"}
    assert reader.call_count == 1


def test_repository_root_link_to_sibling_is_rejected(workspace):
    root, repo = workspace
    make_symlink(root / "alias", repo, directory=True)
    with pytest.raises(RepositoryPathError):
        RepositoryScanner(root).scan("alias")


def test_workspace_root_link_is_rejected(workspace):
    root, _ = workspace
    alias = root.parent / "alias-workspaces"
    make_symlink(alias, root, directory=True)
    with pytest.raises(RepositoryPathError):
        RepositoryScanner(alias).scan("demo")


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction safety")
def test_windows_junction_is_pruned_and_cannot_be_repository_root(workspace):
    root, repo = workspace
    outside = root.parent / "outside-junction"
    outside.mkdir()
    write_file(outside, "secret.py", b"DO_NOT_READ")
    link = repo / "junction"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
    )
    assert created.returncode == 0, created.stderr
    assert is_link_or_reparse(link.lstat())
    with patch("app.repository.scanner.read_bounded_file") as reader:
        result = RepositoryScanner(root).scan("demo")
    assert result.files == [] and result.skipped[0].reason == "unsafe_link"
    reader.assert_not_called()
    with pytest.raises(RepositoryPathError):
        RepositoryScanner(repo).scan("junction")


@pytest.mark.skipif(os.name != "nt", reason="Windows opened handle containment")
def test_windows_opened_file_outside_repository_is_rejected_before_read(workspace):
    root, repo = workspace
    safe = write_file(repo, "safe.py", b"inside")
    secret = write_file(root.parent, "outside.py", b"DO_NOT_READ")
    original_open = os.open

    def replaced_open(*_args, **_kwargs):
        return original_open(secret, os.O_RDONLY | os.O_BINARY)

    with patch("app.repository.paths.os.open", side_effect=replaced_open):
        with pytest.raises(UnsafeRepositoryEntry, match="outside_repository"):
            read_bounded_file(safe, repo, 1024)


def test_file_change_between_inspection_and_open_is_not_accepted(workspace):
    _, repo = workspace
    first = write_file(repo, "safe.py", b"inside")
    replacement = write_file(repo, "replacement.py", b"different")
    original_open = os.open

    def swap(*args, **kwargs):
        if Path(args[0]).name == "safe.py":
            replacement.replace(first)
        return original_open(*args, **kwargs)

    with patch("app.repository.paths.os.open", side_effect=swap):
        with pytest.raises(UnsafeRepositoryEntry, match="changed_during_scan"):
            read_bounded_file(first, repo, 1024)


def test_unreadable_file_and_nested_directory_are_reported_without_raw_errors(workspace):
    root, repo = workspace
    write_file(repo, "blocked.py")
    write_file(repo, "locked/secret.py")
    original_scandir = os.scandir

    def denied_scandir(path):
        if Path(path).name == "locked":
            raise PermissionError("private path detail")
        return original_scandir(path)

    with patch("app.repository.scanner.os.scandir", side_effect=denied_scandir), patch(
        "app.repository.scanner.read_bounded_file", side_effect=PermissionError("private path detail")
    ):
        result = RepositoryScanner(root).scan("demo")
    assert result.skipped[0].reason == "unreadable"
    assert result.pruned_directories[0].reason == "unreadable"
    assert result.files_scanned == 1 and "private path detail" not in repr(result)


def test_unreadable_root_fails_with_safe_error(workspace):
    root, _ = workspace
    with patch("app.repository.scanner.os.scandir", side_effect=PermissionError("private path detail")):
        with pytest.raises(RepositoryScanError) as error:
            RepositoryScanner(root).scan("demo")
    assert "private path detail" not in str(error.value)


def test_scanner_logs_only_metadata(workspace, caplog):
    root, repo = workspace
    write_file(repo, "a.py", b"private-code-contents")
    with caplog.at_level(logging.INFO, logger="app.repository.scanner"):
        RepositoryScanner(root).scan("demo")
    assert "supported=1" in caplog.text
    assert "private-code-contents" not in caplog.text and str(root) not in caplog.text


@pytest.mark.parametrize("field,value", [
    ("repository_max_file_bytes", 0), ("repository_max_file_bytes", 10485761),
    ("repository_max_entries", 0), ("repository_max_entries", 100001),
    ("repository_max_total_bytes", 0), ("repository_max_total_bytes", 209715201),
])
def test_settings_bound_scanner_resources(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_scanner_factory_uses_fixed_workspace_and_configured_limits():
    settings = Settings(
        _env_file=None, repository_max_file_bytes=11,
        repository_max_entries=22, repository_max_total_bytes=33,
    )
    with patch("app.repository.scanner.get_settings", return_value=settings), patch(
        "app.repository.scanner.RepositoryScanner"
    ) as factory:
        create_repository_scanner()
    assert factory.call_args.args[0].name == "workspaces"
    assert factory.call_args.kwargs == dict(max_file_bytes=11, max_entries=22, max_total_bytes=33)


def test_scan_http_returns_real_metadata_without_code_and_calls_no_external_service(workspace):
    root, repo = workspace
    write_file(repo, "src/a.py", b"private-code-contents\n")
    write_file(repo, "notes.txt", b"notes")
    with patch("app.api.repositories.create_repository_scanner", return_value=RepositoryScanner(root)), patch(
        "app.embedding.factory.EmbeddingProviderFactory.create"
    ) as embedding, patch("app.retrieval.vector_store.create_qdrant_client") as qdrant, TestClient(app) as http:
        response = http.post("/api/repositories/scan", json={"repository": "demo"})
        assert http.get("/health").status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["repository"] == "demo" and body["files_scanned"] == 2
    assert body["supported_files"] == body["skipped_files"] == 1
    assert body["files"][0] == dict(path="src/a.py", language="python", size_bytes=22, line_count=1)
    assert body["skipped"] == [dict(path="notes.txt", reason="unsupported_extension")]
    assert "private-code-contents" not in response.text and str(root) not in response.text
    embedding.assert_not_called()
    qdrant.assert_not_called()


@pytest.mark.parametrize("body", [
    {}, {"repository": "../outside"}, {"repository": "CON"}, {"repository": 12},
    {"repository": "demo", "path": "D:/secret"}, {"repository": "demo", "extra": 1},
])
def test_invalid_http_request_does_not_construct_scanner(body):
    with patch("app.api.repositories.create_repository_scanner") as factory, TestClient(app) as http:
        assert http.post("/api/repositories/scan", json=body).status_code == 422
    factory.assert_not_called()


def test_http_missing_repository_returns_404(workspace):
    root, _ = workspace
    with patch("app.api.repositories.create_repository_scanner", return_value=RepositoryScanner(root)), TestClient(app) as http:
        response = http.post("/api/repositories/scan", json={"repository": "missing"})
    assert response.status_code == 404 and str(root) not in response.text


@pytest.mark.parametrize("error,status", [
    (RepositoryPathError("Repository directories must not be links or reparse points."), 403),
    (RepositoryScanError("Repository directory could not be accessed."), 403),
    (RepositoryScanLimitError("Repository exceeds the scan entry limit."), 413),
])
def test_http_domain_errors_have_explicit_status(error, status):
    scanner = SimpleNamespace(scan=lambda _: None)
    with patch("app.api.repositories.create_repository_scanner", return_value=scanner), patch.object(
        scanner, "scan", side_effect=error
    ), TestClient(app) as http:
        response = http.post("/api/repositories/scan", json={"repository": "demo"})
    assert response.status_code == status and response.json() == {"detail": str(error)}
