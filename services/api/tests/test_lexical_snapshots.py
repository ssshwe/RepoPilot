import asyncio
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.core.config import Settings
from app.embedding.identity import embedding_identity
from app.retrieval.snapshot_models import (
    IndexedRepository, LexicalSnapshot, SnapshotBusy, SnapshotError, SnapshotNotReady, SnapshotScope, digest,
)
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import QdrantImageReader, SnapshotCoordinator
from app.retrieval.tokenizer import CodeTokenizer
from app.retrieval.vector_store import QdrantVectorStore


def configuration():
    return {"dimension": 3, "distance": "Cosine", "embedding": embedding_identity(
        Settings(_env_file=None, embedding_model="test", embedding_base_url="http://localhost:11434/v1"), "test"), "chunking": None}


def image(repository="demo", files=None, **scope_updates):
    scope = SnapshotScope(**({"backend_id": "sha256:" + "a" * 64, "collection": "code", "repository": repository} | scope_updates))
    files = files if files is not None else {"a.py": "refund()\n", "b.py": "wallet()\n"}
    records = []
    for path, content in files.items():
        identity = str(uuid5(NAMESPACE_URL, repository + path + content))
        records.append({"id": identity, "vector": [1., 0., 0.], "payload": {
            "chunk_id": identity, "repository": repository, "path": path, "language": "python",
            "start_line": 1, "end_line": len(content.splitlines()), "content": content,
            "embedding_model": "test", "schema_version": "code-points-v1"}})
    return IndexedRepository.from_records(scope, configuration(), records)


class Reader:
    def __init__(self, *images):
        self.images = {item.scope.repository: item for item in images}

    def scope(self, repository):
        return self.images[repository].scope

    async def capture(self, repository):
        return self.images[repository]


def publish(store, value):
    snap = LexicalSnapshot.build(value, CodeTokenizer())
    with store.rebuilding(value.scope, value.source_sha256) as ticket:
        store.publish(ticket, snap)
    return snap


def test_restart_empty_corpus_and_immutable_metadata(tmp_path):
    store = SnapshotStore(tmp_path / "snapshots.sqlite3")
    value, empty = image(), image("empty", {})
    snap = publish(store, value)
    publish(store, empty)
    restarted = SnapshotStore(store.path)
    loaded = restarted.load_ready(value.scope).snapshot
    assert loaded.to_dict() == snap.to_dict() and loaded.index.index_revision == value.index_revision
    assert restarted.load_ready(empty.scope).snapshot.tokens == ()
    with pytest.raises(TypeError):
        loaded.index.configuration["embedding"]["embedding_model"] = "other"
    with pytest.raises(TypeError):
        loaded.tokens[0][0] = "changed"


def test_source_edit_delete_and_explicit_rebuild_remove_stale_chunks(tmp_path):
    async def scenario():
        source = tmp_path / "a.py"
        source.write_bytes(b"refund()\n")
        original = image(files={"a.py": source.read_text(), "b.py": "old_wallet()\n"})
        reader = Reader(original)
        store = SnapshotStore(tmp_path / "index.db")
        coordinator = SnapshotCoordinator(store, reader)
        old = await coordinator.rebuild("demo", expected_source_sha256=original.source_sha256)
        source.unlink()
        assert store.load_ready(original.scope).snapshot.to_dict() == old.to_dict()
        new = image(files={"new.py": "credit()\n"})
        async def replace_index():
            assert store.status(original.scope)["state"] == "rebuilding"
            with pytest.raises(SnapshotNotReady):
                store.load_ready(original.scope)
            reader.images["demo"] = new
        fresh = await coordinator.rebuild("demo", expected_source_sha256=new.source_sha256, mutate=replace_index)
        assert {c.source.path for c in fresh.index.chunks} == {"new.py"}
        assert set(c.chunk_id for c in fresh.index.chunks).isdisjoint(c.chunk_id for c in old.index.chunks)
        assert fresh.revision != old.revision
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["exception", "cancel", "silent_partial"])
def test_failed_rebuild_never_serves_old_lexical_with_partial_dense(tmp_path, failure):
    async def scenario():
        old, intended, partial = image(), image(files={"new.py": "new()\n", "also.py": "also()\n"}), image(files={"new.py": "new()\n"})
        reader = Reader(old)
        store = SnapshotStore(tmp_path / "index.db")
        previous = publish(store, old)
        coordinator = SnapshotCoordinator(store, reader)
        async def fail():
            reader.images["demo"] = partial
            if failure == "exception":
                raise RuntimeError("simulated write failure")
            if failure == "cancel":
                raise asyncio.CancelledError()
        with pytest.raises((RuntimeError, asyncio.CancelledError, SnapshotError)):
            await coordinator.rebuild("demo", expected_source_sha256=intended.source_sha256, mutate=fail)
        state = store.status(old.scope)
        assert state["state"] == "incomplete" and state["active"] == previous.revision
        assert state["target_sources"] == intended.source_sha256
        with pytest.raises(SnapshotNotReady):
            store.load_ready(old.scope)
        reader.images["demo"] = intended
        await coordinator.rebuild("demo", expected_source_sha256=intended.source_sha256)
        assert store.load_ready(old.scope).snapshot.index.source_sha256 == intended.source_sha256
    asyncio.run(scenario())


def test_concurrent_writers_busy_and_stale_tickets_cannot_publish(tmp_path):
    store = SnapshotStore(tmp_path / "index.db")
    value, other = image(), image("other")
    snap = LexicalSnapshot.build(value, CodeTokenizer())
    with store.rebuilding(value.scope, value.source_sha256) as ticket:
        with pytest.raises(SnapshotBusy):
            with SnapshotStore(store.path).rebuilding(value.scope, value.source_sha256):
                pass
        publish(store, other)  # an unrelated repository can still finish
        with pytest.raises(SnapshotError):
            store.publish(ticket, LexicalSnapshot.build(other, CodeTokenizer()))
        store.publish(ticket, snap)
    with pytest.raises(SnapshotError):
        store.publish(ticket, snap)


def test_stale_readers_do_not_invalidate_new_epochs(tmp_path):
    store = SnapshotStore(tmp_path / "index.db")
    old, new = image(), image(files={"new.py": "new()\n"})
    publish(store, old)
    stale = store.load_ready(old.scope)
    publish(store, new)
    store.invalidate(old.scope, stale.ticket)
    assert store.status(old.scope)["state"] == "ready"
    with pytest.raises(SnapshotNotReady):
        store.assert_current(stale)


@pytest.mark.parametrize("when", ["before", "during", "epoch"])
def test_use_discards_changed_dense_or_snapshot_epoch(tmp_path, when):
    async def scenario():
        old, new = image(), image(files={"new.py": "new()\n"})
        store = SnapshotStore(tmp_path / "index.db")
        reader = Reader(old)
        publish(store, old)
        coordinator = SnapshotCoordinator(store, reader)
        called = []
        async def query(snapshot):
            called.append(True)
            if when == "during":
                reader.images["demo"] = new
            if when == "epoch":
                publish(store, old)  # ABA to identical content must still invalidate this epoch
            return "would have been returned"
        if when == "before":
            reader.images["demo"] = new
        with pytest.raises(SnapshotError):
            await coordinator.use("demo", query)
        assert bool(called) == (when != "before")
        assert store.status(old.scope)["state"] == ("ready" if when == "epoch" else "incomplete")
    asyncio.run(scenario())


def test_repository_collection_backend_isolation_and_same_snapshot_for_consumers(tmp_path):
    async def scenario():
        first, second = image(), image("other")
        store, reader = SnapshotStore(tmp_path / "index.db"), Reader(first, second)
        a, b = publish(store, first), publish(store, second)
        assert a.revision != b.revision
        assert store.status(image(collection="different").scope)["state"] == "incomplete"
        assert store.status(image(backend_id="sha256:" + "b" * 64).scope)["state"] == "incomplete"
        reader.images["other"] = image("other", {"changed.py": "other()\n"})
        async def channels(snapshot):
            bm25_view, exact_view = snapshot, snapshot
            assert bm25_view is exact_view
            assert {c.source.path for c in snapshot.index.chunks} == {"a.py", "b.py"}
            return snapshot.revision
        assert await SnapshotCoordinator(store, reader).use("demo", channels) == a.revision
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["tokens", "source", "scope", "version", "unicode", "implementation"])
def test_corrupt_saved_generation_marks_scope_incomplete(tmp_path, change):
    store = SnapshotStore(tmp_path / "index.db")
    value = image()
    snap = publish(store, value)
    data = deepcopy(snap.to_dict())
    if change == "tokens": data["documents"][0]["tokens"].append("forged")
    elif change == "source": data["index"]["chunks"][0]["source"]["content"] = "forged()\n"
    elif change == "scope": data["index"]["scope"]["repository"] = "other"
    elif change == "version": data["tokenizer"]["version"] = "unknown"
    elif change == "unicode": data["tokenizer"]["unicode_version"] = "unknown"
    else: data["tokenizer_implementation_sha256"] = "sha256:" + "0" * 64
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE generations SET body=? WHERE revision=?", (json.dumps(data).encode(), snap.revision))
    with pytest.raises(SnapshotNotReady): store.load_ready(value.scope)
    assert store.status(value.scope)["state"] == "incomplete"


def test_vector_metadata_changes_and_malformed_point_identities_are_detected():
    base = image()
    data = base.to_dict()
    data["chunks"][0]["vector_sha256"] = "sha256:" + "f" * 64
    assert IndexedRepository.from_dict(data).index_revision != base.index_revision
    data = base.to_dict()
    data["configuration"]["embedding"]["embedding_model"] = "different"
    assert IndexedRepository.from_dict(data).index_revision != base.index_revision
    with pytest.raises(SnapshotError):
        IndexedRepository(base.scope, base.configuration, base.chunks + base.chunks)


@pytest.mark.parametrize("bad", ["foreign", "uuid", "zero", "dimension", "partial_metadata", "schema", "model"])
def test_indexed_point_validation_rejects_incompatible_data(bad):
    scope = image().scope
    identity = str(uuid5(NAMESPACE_URL, "point"))
    point = {"id": identity, "vector": [1., 0., 0.], "payload": {"chunk_id": identity, "repository": "demo", "path": "a.py",
            "language": "python", "start_line": 1, "end_line": 1, "content": "call()\n", "embedding_model": "test", "schema_version": "code-points-v1"}}
    if bad == "foreign": point["payload"]["repository"] = "other"
    elif bad == "uuid": point["payload"]["chunk_id"] = "invalid"
    elif bad == "zero": point["vector"] = [0., 0., 0.]
    elif bad == "dimension": point["vector"] = [1., 0.]
    elif bad == "partial_metadata": point["payload"]["symbol"] = "A.call"
    elif bad == "schema": point["payload"]["schema_version"] = "other"
    else: point["payload"]["embedding_model"] = "other"
    with pytest.raises(SnapshotError): IndexedRepository.from_records(scope, configuration(), [point])


def test_read_only_backup_does_not_change_artifact_bytes(tmp_path):
    store = SnapshotStore(tmp_path / "index.db")
    value = image()
    original = publish(store, value)
    backup = tmp_path / "proof.db"
    store.backup(backup)
    before = backup.read_bytes()
    restored = SnapshotStore(backup, read_only=True)
    assert restored.load_ready(value.scope).snapshot.revision == original.revision
    with pytest.raises(SnapshotError):
        with restored.rebuilding(value.scope, value.source_sha256): pass
    assert backup.read_bytes() == before
    with pytest.raises(FileExistsError): store.backup(backup)


def test_repeated_qdrant_pagination_offset_rejected():
    async def scenario():
        settings = Settings(_env_file=None, embedding_model="test", embedding_base_url="http://localhost:11434/v1")
        from qdrant_client import models
        from types import SimpleNamespace
        client = AsyncMock()
        client.get_collection.return_value = SimpleNamespace(config=SimpleNamespace(
            params=SimpleNamespace(vectors=models.VectorParams(size=3, distance=models.Distance.COSINE)),
            metadata={"repopilot_index": embedding_identity(settings, "test")}))
        client.count.return_value = SimpleNamespace(count=1)
        client.scroll.return_value = ([], 7)
        reader = QdrantImageReader(QdrantVectorStore(client, "code"), settings)
        with pytest.raises(SnapshotError, match="offset"):
            await reader.capture("demo")
    asyncio.run(scenario())


def test_process_death_leaves_rebuilding_and_new_writer_recovers(tmp_path):
    value = image()
    path = tmp_path / "crash.db"
    store = SnapshotStore(path)
    publish(store, value)
    script = """import sys,time
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshot_models import SnapshotScope
store=SnapshotStore(sys.argv[1]);scope=SnapshotScope(sys.argv[2],'code','demo')
with store.rebuilding(scope,sys.argv[3]):
 print('locked',flush=True)
 time.sleep(30)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(path), value.scope.backend_id, value.source_sha256],
                               cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=os.environ | {"PYTHONIOENCODING": "utf-8"})
    try:
        assert process.stdout.readline().strip() == "locked"
        assert store.status(value.scope)["state"] == "rebuilding"
        with pytest.raises(SnapshotBusy):
            with store.rebuilding(value.scope, value.source_sha256): pass
    finally:
        process.kill()
        process.communicate(timeout=10)
    with pytest.raises(SnapshotNotReady): store.load_ready(value.scope)
    fresh = publish(SnapshotStore(path), value)
    assert store.load_ready(value.scope).snapshot.revision == fresh.revision


def test_capture_drift_never_publishes_ready_snapshot(tmp_path):
    async def scenario():
        first, second = image(), image(files={"a.py": "new()\n"})
        reader = Reader(first)
        images = iter((first, second))
        async def changing(repository): return next(images)
        reader.capture = changing
        store = SnapshotStore(tmp_path / "index.db")
        with pytest.raises(SnapshotError, match="drift"):
            await SnapshotCoordinator(store, reader).rebuild("demo", expected_source_sha256=first.source_sha256)
        assert store.status(first.scope)["state"] == "incomplete"
    asyncio.run(scenario())


def test_publication_pointer_and_generation_roll_back_together(tmp_path, monkeypatch):
    store = SnapshotStore(tmp_path / "index.db")
    old, new = image(), image(files={"new.py": "new()\n"})
    previous = publish(store, old)
    original = store._db
    class FailingCommit:
        def __init__(self, db): self.db = db
        def execute(self, sql, *args):
            if sql.startswith("UPDATE states SET state='ready'"):
                raise sqlite3.OperationalError("simulated publication write failure")
            return self.db.execute(sql, *args)
    @contextmanager
    def failure():
        with original() as db:
            yield FailingCommit(db)
    monkeypatch.setattr(store, "_db", failure)
    with pytest.raises(sqlite3.OperationalError): publish(store, new)
    monkeypatch.setattr(store, "_db", original)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 1
    assert store.status(old.scope)["active"] == previous.revision
    assert store.status(old.scope)["state"] == "incomplete"
    publish(store, new)
    assert store.status(new.scope)["state"] == "ready"


def test_unsupported_database_format_is_rejected(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as db: db.execute("PRAGMA user_version=99")
    with pytest.raises(SnapshotError, match="version"): SnapshotStore(path)


def test_invalid_expected_manifest_and_unrelated_indexer_are_rejected(tmp_path):
    store, value = SnapshotStore(tmp_path / "index.db"), image()
    with pytest.raises(SnapshotError):
        with store.rebuilding(value.scope, "not-a-digest"): pass
    assert store.status(value.scope)["reason"] == "not_built"
    with pytest.raises(SnapshotError):
        asyncio.run(SnapshotCoordinator(store, Reader(value)).reindex(None, None))
