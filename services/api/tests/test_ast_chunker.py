from dataclasses import replace
import logging
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from app.core.config import REPOSITORY_ROOT
from app.core.exceptions import ChunkingLimitError
from app.indexing.ast_chunker import ASTCodeChunker, AST_CHUNKING_VERSION, FALLBACK_CHUNKING_VERSION
from app.indexing.chunker import SimpleCodeChunker, create_code_chunker
from app.indexing.models import CodeChunk
from app.parsing.base import CodeParsingError
from app.parsing.registry import ParserRegistry, create_parser_registry
from app.repository.models import RepositoryFile
from app.repository.scanner import RepositoryScanner


def source(content: str, language="java", **overrides) -> RepositoryFile:
    extension = {"java": "java", "python": "py", "javascript": "js", "typescript": "ts",
                 "vue": "vue", "markdown": "md", "json": "json", "yaml": "yaml", "sql": "sql"}[language]
    values = dict(repository="test", path=f"src/Wallet.{extension}", language=language,
                  content=content, size_bytes=len(content.encode("utf-8")), line_count=len(content.splitlines()))
    return RepositoryFile(**(values | overrides))


def check_slices(file, chunks, *, disjoint=True):
    lines = file.content.splitlines(keepends=True)
    seen = set()
    for chunk in chunks:
        assert chunk.content == "".join(lines[chunk.start_line - 1:chunk.end_line])
        assert len(chunk.content.splitlines()) == chunk.end_line - chunk.start_line + 1
        assert (chunk.repository, chunk.path, chunk.language) == (file.repository, file.path, file.language)
        assert len(chunk.content) <= 8192 and chunk.content.strip()
        coverage = set(range(chunk.start_line, chunk.end_line + 1))
        if disjoint:
            assert seen.isdisjoint(coverage)
        seen.update(coverage)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)
    return seen


@pytest.mark.parametrize("path,expected", [
    ("backend/service/RefundService.java", [
        (None, "program", None, 1, 4), ("RefundService", "class_declaration", None, 5, 7),
        ("RefundService.RefundService", "constructor_declaration", "RefundService", 8, 10),
        ("RefundService.refund", "method_declaration", "RefundService", 12, 18),
    ]),
    ("backend/service/WalletService.java", [
        (None, "program", None, 1, 6), ("WalletService", "class_declaration", None, 7, 9),
        ("WalletService.credit", "method_declaration", "WalletService", 10, 18),
        ("WalletService.balanceOf", "method_declaration", "WalletService", 20, 22),
    ]),
])
def test_real_java_methods_stay_whole_and_class_body_is_not_duplicated(path, expected):
    scan = RepositoryScanner(REPOSITORY_ROOT / "workspaces").scan("demo-repository")
    file = next(file for file in scan.files if file.path == path)
    result = ASTCodeChunker().chunk_file_result(file)
    assert result.strategy == "ast" and result.fallback_reason is None
    assert [(c.symbol, c.node_type, c.parent_symbol, c.start_line, c.end_line) for c in result.chunks] == expected
    assert all(c.chunking_strategy == "ast" and c.chunking_version == AST_CHUNKING_VERSION for c in result.chunks)
    covered = check_slices(file, result.chunks)
    assert all(i in covered for i, line in enumerate(file.content.splitlines(), 1)
               if line.strip(" \t{}();,"))


def test_real_typescript_interface_is_one_semantic_chunk():
    scan = RepositoryScanner(REPOSITORY_ROOT / "workspaces").scan("demo-repository")
    file = next(file for file in scan.files if file.path == "frontend/src/types.ts")
    chunk, = ASTCodeChunker().chunk_file(file)
    assert (chunk.symbol, chunk.node_type, chunk.start_line, chunk.end_line) == ("RefundRequest", "interface_declaration", 1, 5)
    assert chunk.content == file.content


def test_javascript_fixture_chunks_have_exact_boundaries():
    file = source((Path(__file__).parent / "fixtures/parsing/refund.js").read_text(encoding="utf-8"), "javascript")
    chunks = ASTCodeChunker().chunk_file(file)
    assert [(c.symbol, c.start_line, c.end_line) for c in chunks] == [(None, 1, 1), ("refund", 2, 7), ("canRefund", 9, 11)]
    check_slices(file, chunks)


def test_python_nested_function_keeps_outer_before_and_after_code_with_no_overlap():
    file = source("""@trace
def refund(amount):
    before = amount
    def validate():
        return before > 0
    after = validate()
    return after
""", "python")
    chunks = ASTCodeChunker().chunk_file(file)
    assert [(c.symbol, c.parent_symbol, c.start_line, c.end_line) for c in chunks] == [
        ("refund", None, 1, 3), ("refund.validate", "refund", 4, 5), ("refund", None, 6, 7),
    ]
    assert "".join(c.content for c in chunks) == file.content
    check_slices(file, chunks)


def test_oversized_method_is_split_inside_its_boundary_with_symbol_retained():
    file = source("class Wallet {\n  void credit() {\n" + "    creditOne();\n" * 125 + "  }\n}\n")
    chunks = ASTCodeChunker(chunk_lines=20, overlap_lines=10).chunk_file(file)
    methods = [c for c in chunks if c.symbol == "Wallet.credit"]
    assert len(methods) == 7
    assert (methods[0].start_line, methods[-1].end_line) == (2, 128)
    assert all(c.end_line - c.start_line + 1 <= 20 and c.parent_symbol == "Wallet" for c in methods)
    assert "".join(c.content for c in methods) == "".join(file.content.splitlines(keepends=True)[1:128])
    check_slices(file, chunks)


def test_character_budget_splits_a_valid_method_before_line_limit():
    file = source("class Wallet {\n  void credit() {\n" + "    creditOne();\n" * 20 + "  }\n}\n")
    chunks = ASTCodeChunker(max_chunk_characters=50).chunk_file(file)
    assert len([c for c in chunks if c.symbol == "Wallet.credit"]) > 1
    assert all(len(c.content) <= 50 for c in chunks)
    check_slices(file, chunks)


def test_large_class_is_declaration_and_methods_instead_of_a_whole_class_vector():
    file = source("class Wallet {\n  int balance;\n" + "".join(
        f"  int get{i}() {{ return {i}; }}\n" for i in range(150)
    ) + "}\n")
    chunks = ASTCodeChunker().chunk_file(file)
    assert len(chunks) == 151
    assert chunks[0].symbol == "Wallet" and chunks[0].end_line == 2
    assert {c.symbol for c in chunks[1:]} == {f"Wallet.get{i}" for i in range(150)}
    assert not any(c.start_line == 1 and c.end_line == file.line_count for c in chunks)
    check_slices(file, chunks)


def test_short_getter_and_setter_are_kept_individually_without_fake_padding():
    file = source("class Wallet {\n  int get() { return 0; }\n  void set(int n) {}\n}\n")
    chunks = ASTCodeChunker().chunk_file(file)
    assert [(c.symbol, c.start_line, c.end_line) for c in chunks] == [("Wallet", 1, 1), ("Wallet.get", 2, 2), ("Wallet.set", 3, 3)]
    check_slices(file, chunks)


def test_anonymous_java_body_remains_in_outer_chunk_without_invented_symbol():
    file = source("""class Wallet {
  void credit() {
    Runnable job = () -> { class Local { void hidden() {} } };
    Object value = new Object() { void hidden() {} };
  }
}
""")
    chunks = ASTCodeChunker().chunk_file(file)
    assert [c.symbol for c in chunks] == ["Wallet", "Wallet.credit"]
    assert "void hidden()" in chunks[-1].content and "Runnable job" in chunks[-1].content
    check_slices(file, chunks)


def test_unbound_js_callback_remains_module_text_without_hidden_global_symbol():
    file = source("values.map(n => { function hidden() {} return n; });\n", "javascript")
    chunk, = ASTCodeChunker().chunk_file(file)
    assert chunk.symbol is None and chunk.node_type == "program" and chunk.content == file.content


@pytest.mark.parametrize("language,content", [
    ("java", "class Wallet { void a() {} void a(int n) {} }\n"),
    ("javascript", "function a() {} function b() {}\n"),
    ("typescript", "type A = string; type B = number;\n"),
])
def test_adjacent_inline_declarations_fallback_without_mislabeled_symbols(language, content):
    file = source(content, language)
    result = ASTCodeChunker().chunk_file_result(file)
    assert result.strategy == "fallback" and result.fallback_reason == "shared_line_boundaries"
    assert all(c.symbol is None and c.node_type is None for c in result.chunks)
    assert result.chunks[0].content == content


@pytest.mark.parametrize("language,content", [
    ("java", "class Wallet { void bad( {\n"), ("python", "def bad(:\n"),
    ("javascript", "function bad( {\n"), ("typescript", "interface Bad { a: }\n"),
])
def test_syntax_error_is_per_file_fallback_and_next_file_still_parses(language, content):
    chunker = ASTCodeChunker()
    result = chunker.chunk_file_result(source(content, language))
    assert result.fallback_reason == "syntax_error" and result.strategy == "fallback"
    assert result.chunks[0].content == content
    assert chunker.chunk_file_result(source("class Valid {}\n")).strategy == "ast"


@pytest.mark.parametrize("language,content", [
    ("vue", '<script setup>const n = 1</script>\n<template>{{ n }}</template>\n'),
    ("markdown", "# title\n\nrefund\n"), ("json", '{"amount": 1}\n'),
    ("yaml", "amount: 1\n"), ("sql", "SELECT amount FROM orders;\n"),
])
def test_unsupported_language_reuses_simple_content_and_ranges_with_fallback_metadata(language, content):
    file = source(content, language)
    result = ASTCodeChunker(chunk_lines=2, overlap_lines=1).chunk_file_result(file)
    legacy = SimpleCodeChunker(chunk_lines=2, overlap_lines=1).chunk_file(file)
    assert result.fallback_reason == "unsupported_language"
    assert [(c.start_line, c.end_line, c.content) for c in result.chunks] == [(c.start_line, c.end_line, c.content) for c in legacy]
    assert all(c.chunking_strategy == "fallback" and c.chunking_version == FALLBACK_CHUNKING_VERSION for c in result.chunks)
    assert all(c.symbol is None and c.parent_symbol is None and c.node_type is None for c in result.chunks)
    assert result.chunks[0].chunk_id != legacy[0].chunk_id
    check_slices(file, result.chunks, disjoint=False)


class BrokenParser:
    language = "java"

    def parse(self, file):
        raise CodeParsingError("DO_NOT_LOG_PARSER_CONTENT")


def test_parser_failure_falls_back_without_logging_exception_or_source(caplog):
    file = source("// DO_NOT_LOG_SOURCE\nclass Wallet {}\n")
    with caplog.at_level(logging.INFO):
        result = ASTCodeChunker(registry=ParserRegistry([BrokenParser()])).chunk_file_result(file)
    assert result.fallback_reason == "parser_error" and result.chunks[0].content == file.content
    assert "strategy=fallback" in caplog.text and "chunks=1" in caplog.text
    assert "DO_NOT_LOG" not in caplog.text


def test_native_parser_failure_is_converted_to_controlled_fallback():
    chunker = ASTCodeChunker()
    file = source("class Wallet {}\n")
    with patch("app.parsing.tree_sitter_parser.Parser", side_effect=ValueError("PRIVATE_NATIVE_DETAIL")):
        result = chunker.chunk_file_result(file)
    assert result.fallback_reason == "parser_error" and result.chunks[0].content == file.content
    assert chunker.chunk_file_result(file).strategy == "ast"


def test_programming_errors_are_not_silently_classified_as_source_parse_failures():
    chunker = ASTCodeChunker()
    with patch.object(chunker.registry, "parse", side_effect=RuntimeError("programming bug")):
        with pytest.raises(RuntimeError):
            chunker.chunk_file(source("class Wallet {}\n"))


@pytest.mark.parametrize("change", [
    {"repository": "foreign"}, {"path": "other.java"},
    {"bad_symbol": {"end_line": 999}}, {"bad_symbol": {"end_byte": 999}},
    {"bad_symbol": {"start_byte": 0.0}}, {"bad_symbol": {"symbol": "invented"}},
    {"duplicate": True},
])
def test_inconsistent_parser_metadata_is_not_used_for_chunk_citations(change):
    file = source("class Wallet {}\n")
    parsed = create_parser_registry().parse(file)
    if "bad_symbol" in change:
        parsed = replace(parsed, symbols=(replace(parsed.symbols[0], **change["bad_symbol"]),))
    elif "duplicate" in change:
        parsed = replace(parsed, symbols=parsed.symbols * 2)
    else:
        parsed = replace(parsed, **change)
    registry = create_parser_registry()
    with patch.object(registry, "parse", return_value=parsed):
        result = ASTCodeChunker(registry=registry).chunk_file_result(file)
    assert result.fallback_reason == "parser_error" and result.chunks[0].symbol is None


@pytest.mark.parametrize("language", ["java", "python", "javascript", "typescript", "vue", "markdown"])
@pytest.mark.parametrize("content", ["", " \t\n\n"])
def test_empty_files_have_no_chunks_and_explicit_strategy(language, content):
    result = ASTCodeChunker().chunk_file_result(source(content, language))
    assert result.chunks == ()
    assert result.strategy == ("fallback" if language in {"vue", "markdown"} else "ast")


@pytest.mark.parametrize("language,content", [
    ("java", "// comment\n"), ("python", "# comment\n"),
    ("javascript", "// comment\n"), ("typescript", "// comment\n"),
])
def test_valid_file_without_declarations_is_ast_module_text(language, content):
    chunk, = ASTCodeChunker().chunk_file(source(content, language))
    assert chunk.content == content and chunk.chunking_strategy == "ast" and chunk.symbol is None


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("trailing", [False, True])
def test_chinese_and_newlines_keep_exact_inclusive_ranges(newline, trailing):
    file = source(newline.join(["// 钱包", "class 钱包 {", "  void 入账() {}", "}"]) + (newline if trailing else ""), line_count=999)
    chunks = ASTCodeChunker().chunk_file(file)
    assert [(c.symbol, c.start_line, c.end_line) for c in chunks] == [(None, 1, 1), ("钱包", 2, 2), ("钱包.入账", 3, 3)]
    check_slices(file, chunks)


def test_ids_are_stable_uuids_and_change_for_content_identity_and_version():
    file = source("class Wallet {\n  int get() { return 1; }\n}\n")
    chunker = ASTCodeChunker()
    first = chunker.chunk_file(file)
    assert first == chunker.chunk_file(file)
    assert all(UUID(c.chunk_id).version == 5 for c in first)
    for changed in [replace(file, repository="other"), replace(file, path="src/Other.java"),
                    replace(file, content=file.content.replace("return 1", "return 2"))]:
        assert chunker.chunk_file(changed)[-1].chunk_id != first[-1].chunk_id
    with patch("app.indexing.ast_chunker.AST_CHUNKING_VERSION", "ast-v2"):
        assert chunker.chunk_file(file)[-1].chunk_id != first[-1].chunk_id
    assert ASTCodeChunker(overlap_lines=0).chunk_file(file) == first  # overlap is fallback-only
    assert ASTCodeChunker(chunk_lines=50).chunk_file(file)[-1].chunk_id != first[-1].chunk_id


def test_repeated_overloads_on_separate_lines_have_distinct_chunk_ids():
    chunks = ASTCodeChunker().chunk_file(source("class Wallet {\n  void credit() {}\n  void credit(int n) {}\n}\n"))
    methods = [c for c in chunks if c.symbol == "Wallet.credit"]
    assert len(methods) == 2 and methods[0].chunk_id != methods[1].chunk_id


@pytest.mark.parametrize("language,content", [
    ("java", "class Wallet { void credit() {} }\n"), ("vue", "<template>too long</template>\n"),
])
def test_oversized_single_line_is_rejected_not_truncated_or_hidden_by_fallback(language, content):
    with pytest.raises(ChunkingLimitError, match="character limit"):
        ASTCodeChunker(max_chunk_characters=10).chunk_file(source(content, language))


def test_actual_utf8_byte_budget_is_checked_before_parser_or_fallback():
    chunker = ASTCodeChunker(max_file_bytes=4)
    with patch.object(chunker.registry, "parse") as parser:
        with pytest.raises(ChunkingLimitError, match="file byte limit"):
            chunker.chunk_file(source("// 钱包", size_bytes=1))
    parser.assert_not_called()


def test_chunk_count_limit_rejects_entire_file_before_first_yield(caplog):
    chunker = ASTCodeChunker(max_chunks=1)
    file = source("class Wallet {\n  void credit() {}\n}\n")
    with caplog.at_level(logging.INFO), pytest.raises(ChunkingLimitError, match="chunk count"):
        next(chunker.iter_file_chunks(file))
    assert "status=failed" in caplog.text and "strategy=ast" not in caplog.text


@pytest.mark.parametrize("options", [
    {"chunk_lines": 0}, {"overlap_lines": 60}, {"max_chunk_characters": 8193},
    {"max_chunk_characters": 0}, {"max_chunks": 0}, {"max_file_bytes": 0},
])
def test_invalid_configuration_is_rejected(options):
    with pytest.raises(ValueError):
        ASTCodeChunker(**options)


def test_legacy_simple_factory_ids_and_debug_api_are_not_switched_to_ast():
    assert isinstance(create_code_chunker(), SimpleCodeChunker)
    file = source("class Wallet {}\n")
    chunk, = SimpleCodeChunker().chunk_file(file)
    assert (chunk.symbol, chunk.node_type, chunk.chunking_strategy, chunk.chunking_version) == (None, None, "simple", "line-window-v1")
    assert chunk.chunk_id == "64e7b6de-dffe-5bce-89f3-cb02d9a1b525"  # frozen line-window-v1 identity
    assert CodeChunk("id", "r", "p", "java", 1, 1, "code").chunking_strategy == "simple"


def test_ast_chunking_does_not_create_embedding_or_qdrant_clients():
    with patch("app.embedding.factory.EmbeddingProviderFactory.create") as embedding, patch(
        "app.retrieval.vector_store.create_qdrant_client",
    ) as qdrant:
        assert ASTCodeChunker().chunk_file(source("class Wallet {}\n"))[0].chunking_strategy == "ast"
    embedding.assert_not_called()
    qdrant.assert_not_called()
