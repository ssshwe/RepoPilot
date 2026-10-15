"""Explicit lexical behavior, including term frequencies and shared query/document rules."""
from collections import Counter
from dataclasses import replace

import pytest

from app.retrieval.candidates import SourceMetadata
from app.retrieval.tokenizer import CodeTokenizer, TokenizationLimitError


@pytest.mark.parametrize("text, expected", [
    ("RefundService", ["refundservice", "refund", "service"]),
    ("refund_order", ["refund_order", "refund", "order"]),
    ("HTTPServer", ["httpserver", "http", "server"]),
    ("getHTTPResponse2", ["gethttpresponse2", "get", "http", "response", "2"]),
    ("XMLHTTPRequest", ["xmlhttprequest", "xmlhttp", "request"]),
    ("XML_HTTP2_client", ["xml_http2_client", "xml", "http", "2", "client"]),
    ("UUID4", ["uuid4", "uuid", "4"]),
    ("RefundDTO", ["refunddto", "refund", "dto"]),
    ("__get__", ["__get__", "get"]),
    ("refund_refund", ["refund_refund", "refund"]),
    ("caféService", ["caféservice", "café", "service"]),
    ("userID42", ["userid42", "user", "id", "42"]),
    ("退款订单", ["退款", "款订", "订单"]),
    ("钱", ["钱"]),
    ("退款退款", ["退款", "款退", "退款"]),
    ("refund订单Id", ["refund订单id", "refund", "订单", "id"]),
    ("退款_订单", ["退款_订单", "退款", "订单"]),
    ("支付💳失败", ["支付", "失败"]),
    ("退款，订单", ["退款", "订单"]),
    ("ＲｅｆｕｎｄＳｅｒｖｉｃｅ", ["refundservice", "refund", "service"]),
    ("ＨＴＴＰ２Client", ["http2client", "http", "2", "client"]),
    ("\U00020000\U00020001\U00020002", ["\U00020000\U00020001", "\U00020001\U00020002"]),
    ("Amount must be positive", ["amount", "must", "be", "positive"]),
    ("", []), (" \t\n", []), ("?!💳", []), ("___", []),
    ("refund refund", ["refund", "refund"]),
    ("HTTP-400", ["http", "400"]),
])
def test_exact_identifier_language_and_error_tokens(text, expected):
    assert CodeTokenizer().tokenize(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("backend/service/RefundService.java", ["backend/service/refundservice.java", "refundservice.java", "backend", "service", "refundservice", "refund", "service", "java"]),
    (r"backend\service\RefundService.java", ["backend/service/refundservice.java", "refundservice.java", "backend", "service", "refundservice", "refund", "service", "java"]),
    (r"C:\src\WalletService.java", ["c:/src/walletservice.java", "walletservice.java", "c", "src", "walletservice", "wallet", "service", "java"]),
    (r"\\server\share\A.java", ["//server/share/a.java", "a.java", "server", "share", "a", "java"]),
    ("/src/A.java", ["/src/a.java", "a.java", "src", "a", "java"]),
    ("../src/A.java", ["../src/a.java", "a.java", "src", "a", "java"]),
    (".github/workflows/test.yml", [".github/workflows/test.yml", "test.yml", "github", "workflows", "test", "yml"]),
    ("src/.gitignore", ["src/.gitignore", ".gitignore", "src", "gitignore"]),
    ("README.md", ["readme.md", "readme", "md"]),
    ("types.d.ts", ["types.d.ts", "types", "d", "ts"]),
    ("com.example.RefundService", ["com.example.refundservice", "com", "example", "refundservice", "refund", "service"]),
    ("backend/service/A.java在哪里", ["backend/service/a.java", "a.java", "backend", "service", "a", "java", "在哪", "哪里"]),
    ("src/退款.java", ["src/退款.java", "退款.java", "src", "退款", "java"]),
    ("backend/service/", ["backend/service/", "backend", "service"]),
    ("backend/service", ["backend/service", "backend", "service"]),
    ("`src/My-Service.py`.", ["src/my-service.py", "my-service.py", "src", "my", "service", "py"]),
])
def test_full_paths_basenames_and_segments(text, expected):
    assert CodeTokenizer().tokenize(text) == expected


@pytest.mark.parametrize("text", ["RefundService", "退款 HTTPServer", "src/RefundService.java", "Amount must be positive", "refund订单Id"])
def test_document_and_query_use_identical_core_rules(text):
    tokenizer = CodeTokenizer()
    assert tokenizer.tokenize_query(text) == tokenizer.tokenize(text)


def test_source_fields_share_rules_without_implicit_reweighting():
    tokenizer = CodeTokenizer()
    source = SourceMetadata("src/RefundService.java", "java", 1, 1, "class RefundService {}\n",
                            symbol="RefundService", node_type="class_declaration", chunking_strategy="ast", chunking_version="ast-v1")
    tokens = tokenizer.tokenize_source(source)
    assert tokens == tokenizer.tokenize("\n".join((source.content, source.path, source.symbol)))
    assert Counter(tokens)["refundservice"] == 3  # actual content/path/Symbol occurrences
    legacy = SourceMetadata("a.py", "python", 1, 1, "refund()\n")
    assert tokenizer.tokenize_source(legacy) == tokenizer.tokenize("refund()\n\na.py\n")
    with pytest.raises(TypeError):
        tokenizer.tokenize_source({"path": "a.py"})


def test_bigrams_do_not_cross_script_or_punctuation_and_do_not_translate():
    tokenizer = CodeTokenizer()
    assert tokenizer.tokenize("退款WalletService余额") == ["退款walletservice余额", "退款", "walletservice", "wallet", "service", "余额"]
    assert not set(tokenizer.tokenize_query("退款")).intersection(tokenizer.tokenize("RefundService"))


@pytest.mark.parametrize("text", [None, 1, True, ["refund"]])
def test_non_text_inputs_rejected(text):
    with pytest.raises(TypeError):
        CodeTokenizer().tokenize(text)


@pytest.mark.parametrize("query", ["", " \n", "x" * 8193, 1])
def test_query_validation_matches_public_contract(query):
    with pytest.raises(ValueError):
        CodeTokenizer().tokenize_query(query)


@pytest.mark.parametrize("kwargs", [{"max_characters": 0}, {"max_characters": True}, {"max_tokens": 0}, {"max_tokens": 1.5}])
def test_invalid_budgets_rejected(kwargs):
    with pytest.raises(ValueError):
        CodeTokenizer(**kwargs)


def test_budgets_bound_input_normalized_expansion_and_output():
    with pytest.raises(TokenizationLimitError, match="character"):
        CodeTokenizer(max_characters=4).tokenize("refund")
    with pytest.raises(TokenizationLimitError, match="Normalized"):
        CodeTokenizer(max_characters=1).tokenize("\ufb03")  # NFKC expands to ffi
    with pytest.raises(TokenizationLimitError, match="token"):
        CodeTokenizer(max_tokens=2).tokenize("RefundService")
    assert CodeTokenizer(max_tokens=3).tokenize("RefundService") == ["refundservice", "refund", "service"]


def test_punctuation_runs_and_long_identifiers_are_processed_without_path_backtracking():
    tokenizer = CodeTokenizer(max_tokens=10)
    assert tokenizer.tokenize("." * 50000 + "-" * 50000) == []
    assert tokenizer.tokenize("aA" * 50000) == [("aA" * 50000).casefold(), "a", "aa"]


def test_long_hyphenated_runs_and_directory_paths_are_not_retried_per_word():
    tokenizer = CodeTokenizer()
    text = "a-b-" * 5000 + "a"
    assert Counter(tokenizer.tokenize(text)) == {"a": 5001, "b": 5000}
    assert tokenizer.tokenize(text + "/")[0] == text + "/"


@pytest.mark.parametrize("separator", ["--", "---", "..", "..--"])
def test_long_invalid_path_runs_are_consumed_without_repeated_suffix_scans(separator):
    text = ("a" + separator) * 5000 + "a"
    assert CodeTokenizer().tokenize(text) == ["a"] * 5001


def test_version_identity_and_determinism_are_explicit():
    tokenizer = CodeTokenizer()
    spec = tokenizer.specification()
    assert spec["version"] == "code-lexical-v1" and spec["unicode_version"]
    assert spec["source_fields"] == ["content", "path", "symbol"]
    assert spec["stopwords"] is spec["stemming"] is spec["translation"] is False
    assert replace(tokenizer).tokenize("RefundService 退款") == tokenizer.tokenize("RefundService 退款")
