import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings
from app.embedding.identity import embedding_identity
from app.retrieval.bm25 import BM25CandidateChannel
from app.retrieval.candidates import CandidateSearchRequest, SourceMetadata
from app.retrieval.dense import DenseCandidateChannel
from app.retrieval.exact import ExactCandidateBatch, ExactCandidateChannel, ExactMatch, ExactSearchRequest, match_source
from app.retrieval.filtered import FilteredBM25Channel, FilteredDenseChannel
from app.retrieval.scope import ScopedCandidateBatch, ScopedSearchRequest, SearchFilters
from app.retrieval.snapshot_models import IndexedRepository, SnapshotScope, SnapshotNotReady
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import QdrantImageReader, SnapshotCoordinator
from app.retrieval.vector_store import QdrantVectorStore
from tests.test_lexical_snapshots import Reader, configuration, publish


def source(path="backend/wallet/WalletService.java", symbol="WalletService.credit", content='void credit() {\n  fail("Amount must be positive");\n}\n', language="java", start=11):
    return SourceMetadata(path, language, start, start + len(content.splitlines()) - 1, content, symbol,
                          "method_declaration", symbol.rsplit(".", 1)[0] if "." in symbol else None, "ast", "ast-v1")


def records(repository, sources):
    return [{"id": str(uuid5(NAMESPACE_URL, repository + s.path + str(s.start_line) + (s.symbol or "") + s.content)),
             "vector": [1., 0., 0.], "payload": s.to_dict() | {
                 "chunk_id": str(uuid5(NAMESPACE_URL, repository + s.path + str(s.start_line) + (s.symbol or "") + s.content)),
                 "repository": repository, "embedding_model": "test", "schema_version": "code-points-v1"}} for s in sources]


def repo_image(repository="demo", sources=None):
    sources = sources if sources is not None else [source(), source("backend/legacy/WalletService.java"),
        source("module.py", "credit", "def credit():\n    pass\n", "python", 1)]
    config = configuration() | {"chunking": {"chunking_version": "ast-v1", "fallback_chunking_version": "ast-fallback-line-v1",
        "chunk_lines": 60, "overlap_lines": 10, "max_chunk_characters": 8192}}
    return IndexedRepository.from_records(SnapshotScope("sha256:" + "a" * 64, "code", repository), config, records(repository, sources))


@pytest.mark.parametrize("kwargs", [
    {"language": "JAVA"}, {"language": " java"}, {"language": ""}, {"language": True},
    {"path_prefix": ""}, {"path_prefix": "/etc/"}, {"path_prefix": "../"}, {"path_prefix": "a/../b"},
    {"path_prefix": "a//b"}, {"path_prefix": "a\\b"}, {"path_prefix": "C:/src"}, {"path_prefix": "a/*"},
    {"path_prefix": "a/\n"}, {"path_prefix": "a/?x"},
])
def test_filter_input_validation(kwargs):
    with pytest.raises(ValueError): SearchFilters(**kwargs)


def test_prefix_is_literal_case_sensitive_and_directory_boundary_is_explicit():
    target = source("backend/wallet-old/WalletService.java")
    assert SearchFilters(path_prefix="backend/wallet").matches(target)
    assert not SearchFilters(path_prefix="backend/wallet/").matches(target)
    assert not SearchFilters(path_prefix="Backend/").matches(target)
    assert not SearchFilters(language="rust").matches(target)


@pytest.mark.parametrize("query, expected", [
    ("WalletService.credit", ExactMatch("symbol", "WalletService.credit")),
    ("WalletService.missing在哪里？", ExactMatch("symbol", "WalletService.missing")),
    ("credit", ExactMatch("short_symbol", "credit")),
    ("WalletService.java", ExactMatch("filename", "WalletService.java")),
    ("backend/wallet/WalletService.java", ExactMatch("path", "backend/wallet/WalletService.java")),
    ("请查找 `WalletService.credit`", ExactMatch("symbol", "WalletService.credit")),
    ('哪段代码抛出错误 "Amount must be positive"？', ExactMatch("text", "Amount must be positive")),
    ("查找事件字面量 `wallet.credit`", ExactMatch("text", "wallet.credit")),
    ('""', ExactMatch("literal", '""')), ("0", ExactMatch("literal", "0")),
    ("WalletService.credit 和 RefundService.refund", None), ("?!💳", None),
])
def test_explicit_locator_inference_is_deterministic(query, expected):
    assert ExactMatch.infer(query) == expected


@pytest.mark.parametrize("selector, expected", [
    (ExactMatch("symbol", "WalletService.credit"), 2), (ExactMatch("short_symbol", "credit"), 3),
    (ExactMatch("symbol", "WalletService.missing"), 0), (ExactMatch("symbol", "walletservice.credit"), 0),
    (ExactMatch("path", "backend/wallet/WalletService.java"), 1), (ExactMatch("filename", "WalletService.java"), 2),
    (ExactMatch("filename", "Wallet", "partial"), 2),
    (ExactMatch("text", "Amount must be positive"), 2),
    (ExactMatch("literal", '"Amount must be positive"'), 2),
])
def test_exact_locators_same_names_and_missing_symbols(tmp_path, selector, expected):
    async def scenario():
        image, other = repo_image(), repo_image("other")
        store = SnapshotStore(tmp_path / "snapshot.db")
        for value in (image, other): publish(store, value)
        channel = ExactCandidateChannel(SnapshotCoordinator(store, Reader(image, other)))
        batch = await channel.recall(ExactSearchRequest("demo", "explicit locator", match=selector))
        assert len(batch.candidates) == expected and all(c.repository == "demo" for c in batch.candidates)
        assert ExactCandidateBatch.from_dict(batch.to_dict()) == batch
    asyncio.run(scenario())


def test_missing_symbol_never_falls_back_to_visible_code_literal(tmp_path):
    async def scenario():
        image = repo_image(sources=[source(content='void credit() {\n  log("WalletService.missing");\n}\n')])
        store = SnapshotStore(tmp_path / "snapshot.db"); publish(store, image)
        channel = ExactCandidateChannel(SnapshotCoordinator(store, Reader(image)))
        batch = await channel.recall(ScopedSearchRequest("demo", "WalletService.missing"))
        assert not batch.candidates and batch.match.kind == "symbol"
    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_text_literal_and_partial_coordinates_use_actual_indexed_lines(ending):
    code = ending.join(["// 0 in a comment", 'fail("boom"); int n = 0; int m = 1000;', "next"]) + ending
    s = source(content=code, start=20)
    reason = match_source(s, ExactMatch("text", "boom"))
    assert reason.start_line == reason.end_line == 21 and reason.strength == "full"
    assert code[reason.char_start:reason.char_end_exclusive] == "boom"
    number = match_source(s, ExactMatch("literal", "0"))
    assert number.occurrences == 1 and number.start_line == 21
    tail = match_source(s, ExactMatch("text", "next" + ending, "partial"))
    assert tail.start_line == tail.end_line == 22
    partial = match_source(s, ExactMatch("text", "boo", "partial"))
    assert partial.strength == "partial" and partial.matched_value == "boo"


def test_literal_matching_preserves_raw_escaping_and_ignores_comments_and_identifier_substrings():
    s = source(content='// "ignore" 0\nint x = 1000; String s = "A\\nB"; boolean v = trueValue;\n')
    assert match_source(s, ExactMatch("literal", "0")) is None
    assert match_source(s, ExactMatch("literal", "true")) is None
    assert match_source(s, ExactMatch("text", "ignore")) is None
    assert match_source(s, ExactMatch("literal", '"A\\nB"')).strength == "full"
    assert match_source(s, ExactMatch("text", "A\nB")) is None


def test_exact_reason_score_and_scope_tampering_are_rejected(tmp_path):
    async def scenario():
        value = repo_image();store = SnapshotStore(tmp_path / "snapshot.db");publish(store, value)
        channel = ExactCandidateChannel(SnapshotCoordinator(store, Reader(value)))
        original = (await channel.recall(ExactSearchRequest("demo", "credit", match=ExactMatch("short_symbol", "credit")))).to_dict()
        for change in ("scope", "score", "reason"):
            data = deepcopy(original)
            if change == "scope": data["scope_fingerprint"] = "sha256:" + "0" * 64
            elif change == "score": data["candidates"][0]["channel_scores"]["exact"] += 1
            else: next(iter(data["reasons"].values()))["start_line"] = 999
            with pytest.raises(ValueError): ExactCandidateBatch.from_dict(data)
    asyncio.run(scenario())


def test_unfiltered_bm25_ranks_scores_unchanged_and_incomplete_cannot_bypass(tmp_path):
    async def scenario():
        value = repo_image();store = SnapshotStore(tmp_path / "snapshot.db");publish(store, value)
        coordinator = SnapshotCoordinator(store, Reader(value))
        request = ScopedSearchRequest("demo", "WalletService.credit")
        base = await BM25CandidateChannel(coordinator).recall(request)
        filtered = await FilteredBM25Channel(coordinator).recall(request)
        assert [c.to_dict() for c in base.candidates] == [c.to_dict() for c in filtered.candidates]
        assert ScopedCandidateBatch.from_dict(filtered.to_dict()) == filtered
        with store.rebuilding(value.scope, value.source_sha256):
            for channel in (FilteredBM25Channel(coordinator), ExactCandidateChannel(coordinator)):
                with pytest.raises(SnapshotNotReady): await channel.recall(ScopedSearchRequest("demo", "?!💳"))
    asyncio.run(scenario())


def test_dense_bm25_exact_share_filters_before_limit_and_default_dense_stays_compatible(tmp_path):
    class Provider:
        model_name = "test"
        calls = 0
        async def embed_texts(self, texts):
            self.calls += 1
            return [[1., 0., 0.]]
    async def scenario():
        settings = Settings(_env_file=None, embedding_model="test", embedding_base_url="http://localhost:11434/v1", qdrant_collection="code")
        client = AsyncQdrantClient(":memory:")
        try:
            qstore = QdrantVectorStore(client, "code")
            await qstore.prepare_index(3, embedding_identity(settings, "test"))
            sources = [source(f"noise/{i}.java", f"A{i}.credit", "credit()\n", start=1) for i in range(70)]
            sources.append(source("target/selected.py", "Target.credit", "def credit():\n    return " + " + ".join(["padding"] * 100) + "\n", "python", 1))
            raw = records("demo", sources)
            target = next(p for p in raw if p["payload"]["path"] == "target/selected.py")
            target["vector"] = [0., 1., 0.]
            await qstore.upsert_points([models.PointStruct(**p) for p in raw])
            profile = repo_image(sources=sources).configuration["chunking"]
            await qstore.record_repository_chunking("demo", dict(profile))
            reader = QdrantImageReader(qstore, settings)
            image = await reader.capture("demo")
            store = SnapshotStore(tmp_path / "snapshot.db")
            coordinator = SnapshotCoordinator(store, reader)
            await coordinator.rebuild("demo", expected_source_sha256=image.source_sha256)
            provider = Provider()
            dense = FilteredDenseChannel(provider, settings, coordinator)
            bm25 = FilteredBM25Channel(coordinator)
            exact = ExactCandidateChannel(coordinator)
            old = await DenseCandidateChannel(provider, qstore, settings, index_revision=image.index_revision).recall(CandidateSearchRequest("demo", "credit", 5))
            unfiltered = await dense.recall(ScopedSearchRequest("demo", "credit", 5))
            assert [c.to_dict() for c in old.candidates] == [c.to_dict() for c in unfiltered.candidates]
            assert target["id"] not in {c.chunk_id for c in (await bm25.recall(ScopedSearchRequest("demo", "credit", 60))).candidates}
            spy = AsyncMock(wraps=client.query_points)
            client.query_points = spy
            request = ScopedSearchRequest("demo", "credit", 1, filters=SearchFilters(language="python", path_prefix="target/"))
            outputs = [await channel.recall(request) for channel in (dense, bm25, exact)]
            assert all([c.chunk_id for c in batch.candidates] == [target["id"]] for batch in outputs)
            assert len({batch.filters.fingerprint("demo") for batch in outputs}) == 1
            assert any(isinstance(c, models.HasIdCondition) and c.has_id == [target["id"]] for c in spy.call_args.kwargs["query_filter"].must)
            before = provider.calls
            empty = ScopedSearchRequest("demo", "credit", filters=SearchFilters(language="rust"))
            empty_batches = [await channel.recall(empty) for channel in (dense, bm25, exact)]
            assert all(not batch.candidates for batch in empty_batches)
            assert provider.calls == before
        finally:
            await client.close()
    asyncio.run(scenario())
