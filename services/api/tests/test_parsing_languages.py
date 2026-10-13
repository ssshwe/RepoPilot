from pathlib import Path

import pytest

from app.parsing.base import SourceSyntaxError
from app.parsing.registry import create_parser_registry
from app.repository.models import RepositoryFile


def source(content: str, language: str) -> RepositoryFile:
    extension = {"python": "py", "javascript": "js", "typescript": "ts"}[language]
    return RepositoryFile("test", f"src/refund.{extension}", language,
                          len(content.encode("utf-8")), len(content.splitlines()), content)


def test_python_decorators_async_methods_and_nested_function_have_lexical_ranges():
    file = source("""import functools
@service
class Wallet:
    @trace
    async def credit(self, amount):
        def validate():
            return amount > 0
        return validate()

def refund():
    return 1
""", "python")
    parsed = create_parser_registry().parse(file)
    assert parsed.root_node_type == "module"
    assert [(s.node_type, s.symbol, s.parent_symbol, s.start_line, s.end_line) for s in parsed.symbols] == [
        ("class_definition", "Wallet", None, 2, 8),
        ("function_definition", "Wallet.credit", "Wallet", 4, 8),
        ("function_definition", "Wallet.credit.validate", "Wallet.credit", 6, 7),
        ("function_definition", "refund", None, 10, 11),
    ]
    encoded = file.content.encode()
    assert encoded[parsed.symbols[1].start_byte:parsed.symbols[1].end_byte].startswith(b"@trace")


def test_javascript_fixture_extracts_exported_function_and_bound_arrow():
    file = source((Path(__file__).parent / "fixtures/parsing/refund.js").read_text(encoding="utf-8"), "javascript")
    symbols = create_parser_registry().parse(file).symbols
    assert [(s.node_type, s.symbol, s.start_line, s.end_line) for s in symbols] == [
        ("function_declaration", "refund", 2, 7), ("arrow_function", "canRefund", 9, 11),
    ]
    assert file.content.encode()[symbols[0].start_byte:symbols[0].end_byte].startswith(b"export function")
    assert file.content.encode()[symbols[1].start_byte:symbols[1].end_byte].startswith(b"canRefund =")


@pytest.mark.parametrize("language", ["javascript", "typescript"])
def test_bound_expressions_object_methods_and_anonymous_callbacks_are_distinguished(language):
    file = source("""const api = {
  refund(n) { return n },
  balance: function internalName() { return 0 }
};
const Wallet = class { credit() { return 1 } };
const validate = function* () { yield true };
values.map(n => { function hidden() {} return n; });
values.map(function callback() { function hiddenAgain() {} });
const { broken } = { broken: n => n };
""", language)
    assert [s.symbol for s in create_parser_registry().parse(file).symbols] == [
        "api.refund", "api.balance", "Wallet", "Wallet.credit", "validate",
    ]


@pytest.mark.parametrize("language", ["javascript", "typescript"])
def test_class_constructor_accessors_fields_and_computed_names(language):
    file = source("""class Wallet {
  constructor() {}
  get balance() { return 0 }
  set balance(value) {}
  credit = (n) => n;
  [dynamicName]() { function hidden() {} }
}
""", language)
    symbols = create_parser_registry().parse(file).symbols
    assert [s.symbol for s in symbols] == [
        "Wallet", "Wallet.constructor", "Wallet.balance", "Wallet.balance", "Wallet.credit",
    ]
    assert symbols[-1].node_type == "arrow_function"
    assert symbols[2].start_byte != symbols[3].start_byte


def test_typescript_named_types_and_abstract_signatures():
    file = source("""export interface Request {
  amount: number;
  credit(n: number): void;
}
export type Id = string;
export abstract class Wallet {
  abstract credit(n: number): void;
  balance = () => 0;
}
export function refund(n: number): number;
export function refund(n: number): number { return n; }
""", "typescript")
    symbols = create_parser_registry().parse(file).symbols
    assert [(s.node_type, s.symbol) for s in symbols] == [
        ("interface_declaration", "Request"), ("method_signature", "Request.credit"),
        ("type_alias_declaration", "Id"), ("abstract_class_declaration", "Wallet"),
        ("abstract_method_signature", "Wallet.credit"), ("arrow_function", "Wallet.balance"),
        ("function_signature", "refund"), ("function_declaration", "refund"),
    ]


def test_typescript_namespace_and_enum_keep_named_lexical_owner():
    file = source("""export namespace Wallet {
  export function credit(n: number) { return n; }
}
export enum Status { READY, DONE }
""", "typescript")
    symbols = create_parser_registry().parse(file).symbols
    assert [(s.node_type, s.symbol, s.parent_symbol) for s in symbols] == [
        ("internal_module", "Wallet", None), ("function_declaration", "Wallet.credit", "Wallet"),
        ("enum_declaration", "Status", None),
    ]


@pytest.mark.parametrize("language,content", [
    ("python", "def refund(:\n  pass\n"),
    ("javascript", "function refund( {"),
    ("typescript", "interface Request { amount: }"),
])
def test_each_core_grammar_reports_recovered_errors_without_source(language, content):
    with pytest.raises(SourceSyntaxError) as error:
        create_parser_registry().parse(source(content, language))
    assert content not in str(error.value)


@pytest.mark.parametrize("language,content,expected", [
    ("python", "# 钱包\nclass 钱包:\n    def 入账(self):\n        return 1\n", "钱包.入账"),
    ("javascript", "// 钱包\nclass 钱包 {\n  入账() { return 1 }\n}\n", "钱包.入账"),
    ("typescript", "// 钱包\nclass 钱包 {\n  入账(): number { return 1 }\n}\n", "钱包.入账"),
])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_core_languages_preserve_chinese_names_and_original_newlines(language, content, expected, newline):
    file = source(content.replace("\n", newline), language)
    parsed = create_parser_registry().parse(file)
    method = parsed.symbols[-1]
    assert method.symbol == expected and method.start_line == 3
    text = file.content.encode()[method.start_byte:method.end_byte].decode()
    assert method.name in text and text in "".join(file.content.splitlines(keepends=True)[2:method.end_line])


@pytest.mark.parametrize("language", ["python", "javascript", "typescript"])
def test_new_grammars_accept_empty_source(language):
    assert create_parser_registry().parse(source("", language)).symbols == ()
