from dataclasses import replace

import pytest

from app.core.config import REPOSITORY_ROOT
from app.parsing.base import CodeParsingError, SourceSyntaxError, UnsupportedLanguageError
from app.parsing.registry import ParserRegistry, create_parser_registry
from app.repository.models import RepositoryFile
from app.repository.scanner import RepositoryScanner


def source(content: str, **overrides) -> RepositoryFile:
    values = dict(
        repository="test", path="src/Wallet.java", language="java", content=content,
        size_bytes=len(content.encode("utf-8", errors="surrogatepass")),
        line_count=len(content.splitlines()),
    )
    return RepositoryFile(**(values | overrides))


@pytest.fixture
def registry():
    return create_parser_registry()


def test_registry_enables_core_languages_and_rejects_duplicate_registration(registry):
    assert registry.supported_languages == ("java", "javascript", "python", "typescript")
    assert registry.get_parser("java").language == "java"
    assert registry.get_parser("vue") is None
    with pytest.raises(ValueError):
        ParserRegistry([registry.get_parser("java"), registry.get_parser("java")])


@pytest.mark.parametrize("extension,language", [
    ("vue", "vue"), ("md", "markdown"), ("json", "json"),
])
def test_unsupported_language_is_explicit_not_a_partial_success(registry, extension, language):
    with pytest.raises(UnsupportedLanguageError):
        registry.parse(source("example", path=f"src/file.{extension}", language=language))


def test_registry_rejects_inconsistent_language_and_detects_uppercase_extension(registry):
    file = source("class Wallet {}", path="src/Wallet.JAVA")
    assert registry.parse(file).symbols[0].symbol == "Wallet"
    with pytest.raises(CodeParsingError):
        registry.parse(replace(file, language="python"))
    with pytest.raises(CodeParsingError):
        registry.get_parser("java").parse(replace(file, language="python"))


@pytest.mark.parametrize("path,expected", [
    ("backend/service/RefundService.java", [
        ("class_declaration", "RefundService", None, 5, 19),
        ("constructor_declaration", "RefundService.RefundService", "RefundService", 8, 10),
        ("method_declaration", "RefundService.refund", "RefundService", 12, 18),
    ]),
    ("backend/service/WalletService.java", [
        ("class_declaration", "WalletService", None, 7, 23),
        ("method_declaration", "WalletService.credit", "WalletService", 10, 18),
        ("method_declaration", "WalletService.balanceOf", "WalletService", 20, 22),
    ]),
])
def test_real_repository_symbols_and_source_ranges(registry, path, expected):
    scan = RepositoryScanner(REPOSITORY_ROOT / "workspaces").scan("demo-repository")
    file = next(f for f in scan.files if f.path == path)
    result = registry.parse(file)
    assert result.root_node_type == "program"
    assert (result.repository, result.path, result.language) == ("demo-repository", path, "java")
    assert [(s.node_type, s.symbol, s.parent_symbol, s.start_line, s.end_line)
            for s in result.symbols] == expected
    encoded = file.content.encode("utf-8")
    lines = file.content.splitlines(keepends=True)
    for symbol in result.symbols:
        node_text = encoded[symbol.start_byte:symbol.end_byte].decode("utf-8")
        whole_lines = "".join(lines[symbol.start_line - 1:symbol.end_line])
        assert node_text in whole_lines
        assert symbol.name in node_text
        assert 1 <= symbol.start_line <= symbol.end_line <= file.line_count


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("trailing_newline", [False, True])
def test_unicode_names_and_byte_offsets_preserve_inclusive_lines(registry, newline, trailing_newline):
    content = newline.join(["// 钱包", "class 钱包 {", "  void 入账() {}", "}"])
    if trailing_newline:
        content += newline
    file = source(content)
    result = registry.parse(file)
    cls, method = result.symbols
    assert (cls.symbol, cls.start_line, cls.end_line) == ("钱包", 2, 4)
    assert (method.symbol, method.start_line, method.end_line) == ("钱包.入账", 3, 3)
    encoded = content.encode("utf-8")
    assert encoded[method.start_byte:method.end_byte].decode("utf-8") == "void 入账() {}"
    assert method.start_byte == len((newline.join(content.splitlines()[:2]) + newline + "  ").encode("utf-8"))
    assert encoded[method.end_byte - 1:method.end_byte] == b"}"
    assert method.start_point == (2, 2)


def test_annotations_nested_classes_and_local_class_scope(registry):
    file = source("""class Outer {
  @Deprecated
  void run() {
    class Local { void work() {} }
  }
  class Inner { Inner() {} void work() {} }
}
""")
    symbols = registry.parse(file).symbols
    assert [s.symbol for s in symbols] == [
        "Outer", "Outer.run", "Outer.run.Local", "Outer.run.Local.work",
        "Outer.Inner", "Outer.Inner.Inner", "Outer.Inner.work",
    ]
    assert (symbols[1].start_line, symbols[1].end_line) == (2, 5)
    assert symbols[3].parent_symbol == "Outer.run.Local"


def test_overloaded_inline_methods_have_distinct_bytes_even_when_name_and_lines_match(registry):
    file = source("class Wallet { void credit() {} void credit(int amount) {} }")
    methods = [s for s in registry.parse(file).symbols if s.node_type == "method_declaration"]
    assert [s.symbol for s in methods] == ["Wallet.credit", "Wallet.credit"]
    assert {(s.start_line, s.end_line) for s in methods} == {(1, 1)}
    assert methods[0].end_byte <= methods[1].start_byte


@pytest.mark.parametrize("content,expected", [
    ("interface Wallet { void credit(); }", ["interface_declaration", "method_declaration"]),
    ("record Order(int amount) { Order {} }", ["record_declaration", "compact_constructor_declaration"]),
    ("enum Status { DONE; void check() {} }", ["enum_declaration", "method_declaration"]),
    ("@interface Flag {}", ["annotation_type_declaration"]),
])
def test_other_named_java_type_declarations(registry, content, expected):
    assert [s.node_type for s in registry.parse(source(content)).symbols] == expected


@pytest.mark.parametrize("content,expected", [
    ("""class Wallet {
  Object value = new Object() { void hidden() {} };
  Runnable job = () -> { class Local { void hidden() {} } };
  void credit() {}
}
""", ["Wallet", "Wallet.credit"]),
    ("enum Status { DONE { void hidden() {} }; void check() {} }", ["Status", "Status.check"]),
])
def test_anonymous_class_and_lambda_do_not_fabricate_outer_method_symbols(registry, content, expected):
    assert [s.symbol for s in registry.parse(source(content)).symbols] == expected


@pytest.mark.parametrize("content", ["", " \r\n\t", "// comment only\n"])
def test_empty_or_comment_only_source_has_no_symbols(registry, content):
    result = registry.parse(source(content))
    assert result.root_node_type == "program"
    assert result.symbols == ()


@pytest.mark.parametrize("content", [
    "class Wallet { void credit( {", "class { }",
    "class Wallet {\u2028 void credit() {}\u2028}",
    "class Wallet {\u2029 void credit() {}\u2029}",
])
def test_recovered_syntax_errors_are_controlled_failures_without_source_in_message(registry, content):
    with pytest.raises(SourceSyntaxError) as error:
        registry.parse(source(content))
    assert str(error.value) == "Source contains syntax errors; AST symbols are unavailable."


def test_unencodable_source_fails_safely_and_next_parse_is_independent(registry):
    with pytest.raises(CodeParsingError, match="UTF-8"):
        registry.parse(source("class Wallet { }\ud800"))
    assert registry.parse(source("class Valid {} ")).symbols[0].symbol == "Valid"
