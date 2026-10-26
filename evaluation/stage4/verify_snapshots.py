"""Task 5: frozen-corpus persistence plus isolated live Qdrant lifecycle receipts."""
import argparse
import asyncio
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "services/api"), str(ROOT)]

from qdrant_client import models
from app.core.config import get_settings
from app.core.exceptions import QdrantUnavailableError
from app.embedding.base import EmbeddingProvider
from app.embedding.identity import embedding_identity
from app.indexing.ast_chunker import create_ast_code_chunker
from app.indexing.service import RepositoryIndexer, prepare_repository
from app.repository.scanner import RepositoryScanner
from app.retrieval.candidates import SourceMetadata
from app.retrieval.snapshot_models import IndexedRepository, LexicalSnapshot, SnapshotError, SnapshotNotReady, SnapshotScope, digest
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshots import QdrantImageReader, SnapshotCoordinator
from app.retrieval.tokenizer import CodeTokenizer
from app.retrieval.vector_store import QdrantVectorStore, create_qdrant_client
from evaluation.retrieval.evaluate import require
from evaluation.stage4.state import file_reference, full_snapshot, read_json, safe_configuration, verify_unchanged, write_new
from evaluation.stage4.verify_tokenizer import replay as replay_tokens

IMPLEMENTATIONS = ("services/api/app/retrieval/snapshot_models.py", "services/api/app/retrieval/snapshot_store.py",
                   "services/api/app/retrieval/snapshots.py", "services/api/app/retrieval/tokenizer.py")


class FrozenReader:
    def __init__(self, images): self.images = images
    def scope(self, repository): return self.images[repository].scope
    async def capture(self, repository): return self.images[repository]


class StorageTestEmbedding(EmbeddingProvider):
    """Explicit synthetic fixture vectors; not real embedding/quality evidence."""
    calls = 0
    @property
    def model_name(self): return "task5-storage-test"
    async def embed_texts(self, texts):
        self.calls += 1
        return [[1., 0., 0.] for _ in texts]
    async def aclose(self): pass


def frozen_images(reference, data):
    snapshot = read_json(ROOT / reference["artifacts"]["index"]["path"])
    metadata = snapshot["config"]["metadata"]
    backend_id = digest({"kind": "saved Qdrant index", "reference": reference["artifacts"]["index"]})
    return {repo: IndexedRepository.from_records(
        SnapshotScope(backend_id, reference["collection"], repo),
        {"dimension": snapshot["config"]["params"]["vectors"]["size"], "distance": "Cosine",
         "embedding": metadata["repopilot_index"], "chunking": metadata["repopilot_chunking"].get(repo)},
        [point for point in snapshot["points"] if point["payload"]["repository"] == repo]) for repo in data["repositories"]}


def restore_in_subprocess(path, scopes):
    script = """import json,sys
from pathlib import Path
sys.path[:0]=[str(Path.cwd()/'services/api'),str(Path.cwd())]
from app.retrieval.snapshot_store import SnapshotStore
from app.retrieval.snapshot_models import SnapshotScope
store=SnapshotStore(Path(sys.argv[1]),read_only=True)
print(json.dumps([store.load_ready(SnapshotScope(**s)).snapshot.revision for s in json.loads(sys.argv[2])]))
"""
    result = subprocess.run([sys.executable, "-c", script, str(path), json.dumps(scopes)],
                            cwd=ROOT, text=True, capture_output=True, timeout=30, check=True)
    return json.loads(result.stdout)


async def verify(args):
    output = args.output.resolve()
    artifacts = output.parent / (output.stem + "_artifacts")
    require(not output.exists() and not artifacts.exists(), "Existing snapshot proof/artifacts must not be overwritten")
    token_path = ROOT / "evaluation/reports/stage4_tokenizer_20261015_task4_final.json"
    replay_tokens(token_path)
    tokens = read_json(token_path)
    reference = read_json(ROOT / tokens["reference_report"]["path"])
    data = read_json(ROOT / tokens["dataset"]["path"])
    settings = get_settings()
    config = safe_configuration(settings)
    implementation = {name: file_reference(ROOT / name) for name in IMPLEMENTATIONS}
    artifacts.mkdir(parents=True)
    runtime = ROOT / ".tools" / ("stage4-task5-" + uuid4().hex)
    store = SnapshotStore(runtime / "snapshots.sqlite3")
    phases, snapshots = [], {}
    def receipt(name, snapshot=None, scope=None, **details):
        scope = scope or snapshot.index.scope
        saved = None
        if snapshot is not None:
            path = artifacts / "snapshots" / (snapshot.revision[7:] + ".json")
            if not path.exists(): write_new(path, snapshot.to_dict())
            else: require(read_json(path) == snapshot.to_dict(), "Immutable proof snapshot differs")
            saved = file_reference(path)
            snapshots[snapshot.revision] = saved
        phases.append({"name": name, "scope": scope.to_dict(), "state": store.status(scope), "snapshot": saved, **details})
        print(name + ": " + store.status(scope)["state"], flush=True)
    images = frozen_images(reference, data)
    offline = SnapshotCoordinator(store, FrozenReader(images))
    frozen_revisions = []
    for repository, image in images.items():
        snap = await offline.rebuild(repository, expected_source_sha256=image.source_sha256)
        frozen_revisions.append(snap.revision)
        receipt("frozen-" + repository, snap)
    restored = restore_in_subprocess(store.path, [i.scope.to_dict() for i in images.values()])
    require(restored == frozen_revisions, "Separate-process snapshot restore differs")
    token_by_id = {r["chunk_id"]: r["tokens"] for r in tokens["chunks"]}
    for image in images.values():
        snap = store.load_ready(image.scope).snapshot
        require(all(list(words) == token_by_id[chunk.chunk_id] for chunk, words in zip(image.chunks, snap.tokens, strict=True)),
                "Task 5 token corpus differs from Task 4")

    client = create_qdrant_client(settings)
    name = "repopilot_stage4_task5_" + uuid4().hex
    attempted, before = False, None
    provider = StorageTestEmbedding()
    try:
        before = await full_snapshot(client)
        production_reader = QdrantImageReader(QdrantVectorStore(client, settings.qdrant_collection), settings)
        production_coordinator = SnapshotCoordinator(store, production_reader)
        production_image = await production_reader.capture("demo-repository")
        live = await production_coordinator.rebuild("demo-repository", expected_source_sha256=production_image.source_sha256)
        receipt("existing-production-read-only", live)

        private = settings.model_copy(update={"qdrant_collection": name, "embedding_model": provider.model_name})
        qstore = QdrantVectorStore(client, name)
        require(name not in before, "Temporary collection name collision")
        attempted = True
        await qstore.prepare_index(3, embedding_identity(private, provider.model_name))
        reader = QdrantImageReader(qstore, private)
        coordinator = SnapshotCoordinator(store, reader)
        indexer = RepositoryIndexer(provider, qstore, private)
        with tempfile.TemporaryDirectory(prefix="repopilot-task5-") as directory:
            workspace = Path(directory).resolve()
            require(workspace.is_relative_to(Path(tempfile.gettempdir()).resolve()), "Fixture directory escaped temporary root")
            for repository in ("alpha", "beta"):
                base = workspace / repository
                base.mkdir()
                (base / "keep.py").write_text(f'def credit(amount):\n    return "{repository}-original"\n', encoding="utf-8")
                (base / "stale.py").write_text('def obsolete():\n    return "stale"\n', encoding="utf-8")
            def prepare(repository):
                return prepare_repository(repository, RepositoryScanner(workspace), create_ast_code_chunker(), private.indexing_max_text_characters)
            alpha = await coordinator.reindex(prepare("alpha"), indexer)
            beta = await coordinator.reindex(prepare("beta"), indexer)
            receipt("initial-alpha", alpha)
            receipt("initial-beta", beta)
            async def scoped_query(snapshot):
                points = await qstore.search_repository_candidates(snapshot.index.scope.repository, [1., 0., 0.], 30)
                by_id = {c.chunk_id: c for c in snapshot.index.chunks}
                require(all(str(p.id) in by_id and p.payload["repository"] == snapshot.index.scope.repository
                            and SourceMetadata.from_payload(p.payload) == by_id[str(p.id)].source for p in points), "Dense/lexical code scope differs")
                return sorted(str(p.id) for p in points)
            require(await coordinator.use("alpha", scoped_query) == sorted(c.chunk_id for c in alpha.index.chunks), "Initial Dense candidate inventory differs")
            (workspace / "alpha/keep.py").write_text('def credit(amount):\n    return "alpha-updated"\n', encoding="utf-8")
            (workspace / "alpha/stale.py").unlink()
            require(await coordinator.use("alpha", scoped_query) == sorted(c.chunk_id for c in alpha.index.chunks), "Read unexpectedly loaded latest files")
            receipt("source-edited-before-reindex", alpha, frozen_code_unchanged=True)
            updated = await coordinator.reindex(prepare("alpha"), indexer)
            require({c.source.path for c in updated.index.chunks} == {"keep.py"}
                    and all("alpha-updated" in c.source.content for c in updated.index.chunks), "Changed/deleted file not reflected")
            receipt("changed-and-deleted-reindexed", updated)
            require(store.load_ready(beta.index.scope).snapshot.revision == beta.revision, "Other repository snapshot changed")

            (workspace / "alpha/keep.py").write_text('def credit(amount):\n    return "alpha-recovered"\n', encoding="utf-8")
            (workspace / "alpha/new.py").write_text('def added():\n    return "new"\n', encoding="utf-8")
            intended = prepare("alpha")
            real_upsert = qstore.upsert_points
            async def partial_write(points):
                await real_upsert(points[:1])
                raise QdrantUnavailableError("Simulated failure after one confirmed Point write")
            qstore.upsert_points = partial_write
            try:
                try:
                    await coordinator.reindex(intended, indexer)
                except QdrantUnavailableError:
                    pass
                else:
                    raise AssertionError("Partial write was unexpectedly accepted")
            finally:
                qstore.upsert_points = real_upsert
            partial = await reader.capture("alpha")
            require(len(partial.chunks) == 1 and store.status(partial.scope)["state"] == "incomplete", "Partial Dense state not retained/blocked")
            try:
                await coordinator.use("alpha", scoped_query)
            except SnapshotNotReady:
                pass
            else:
                raise AssertionError("Old lexical snapshot was exposed with partial Dense data")
            receipt("failed-rebuild-blocked", scope=partial.scope, partial_dense_image=partial.to_dict(), request_blocked=True)
            recovered = await coordinator.reindex(intended, indexer)
            receipt("explicit-full-recovery", recovered)
            require(len(await coordinator.use("alpha", scoped_query)) == len(intended.chunks), "Recovery inventory incomplete")

            for file in (workspace / "alpha").iterdir():
                file.unlink()
            empty = await coordinator.reindex(prepare("alpha"), indexer)
            require(empty.tokens == () and await coordinator.use("alpha", scoped_query) == [], "Empty repository retains stale evidence")
            receipt("delete-all-reindexed-empty", empty)

            records, _ = await client.scroll(name, scroll_filter=qstore.repository_filter("beta"), limit=100, with_vectors=True, with_payload=True)
            point = records[0]
            await real_upsert([models.PointStruct(id=point.id, payload=point.payload, vector=[0., 1., 0.])])
            try:
                await coordinator.use("beta", scoped_query)
            except SnapshotError:
                pass
            else:
                raise AssertionError("Vector drift was not rejected")
            receipt("external-vector-drift-blocked", scope=beta.index.scope, request_blocked=True)
            repaired_beta = await coordinator.reindex(prepare("beta"), indexer)
            receipt("beta-full-recovery", repaired_beta)
            require(repaired_beta.index.source_sha256 == beta.index.source_sha256, "Beta source changed")
            require(workspace.is_relative_to(Path(tempfile.gettempdir()).resolve()), "Unsafe temporary cleanup target")
        database = artifacts / "snapshots.sqlite3"
        store.backup(database)
        final_scopes = [i.scope.to_dict() for i in images.values()] + [live.index.scope.to_dict(), empty.index.scope.to_dict(), repaired_beta.index.scope.to_dict()]
        final_revisions = [store.load_ready(SnapshotScope(**s)).snapshot.revision for s in final_scopes]
        require(restore_in_subprocess(database, final_scopes) == final_revisions, "Final backup/restart validation differs")
        require(implementation == {n: file_reference(ROOT / n) for n in IMPLEMENTATIONS}, "Snapshot implementation drifted during proof")
        verify_unchanged(config, safe_configuration(type(settings)()), "Configuration")
    finally:
        try:
            if attempted and await client.collection_exists(name):
                require(await client.delete_collection(name), "Temporary collection deletion unconfirmed")
            if attempted:
                require(not await client.collection_exists(name), "Temporary collection remains")
            if before is not None:
                verify_unchanged(before, await full_snapshot(client), "All original Qdrant collections")
        finally:
            await provider.aclose()
            await client.close()
    report = {"schema_version": 1, "task": "Stage 4 / Task 5", "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
              "reference_tokens": file_reference(token_path), "implementations": implementation, "configuration": config,
              "phases": phases, "snapshots": snapshots, "database": file_reference(database), "final_scopes": final_scopes,
              "final_revisions": final_revisions, "frozen_index_chunks": sum(len(i.chunks) for i in images.values()),
              "restored_in_separate_process": True, "holdout_queried": False,
              "calls": {"real_embedding_calls": 0, "generation_calls": 0, "storage_fixture_embedding_calls": provider.calls},
              "preservation": {"original_collections_unchanged": True, "temporary_collection_deleted": True, "production_snapshot_built_read_only": True},
              "note": "Actual Qdrant lifecycle verification uses a private 3D synthetic embedding fixture; no retrieval quality claims"}
    write_new(output, report)
    print(f"Complete: {len(phases)} lifecycle phases, {len(final_scopes)} restored scopes; temporary collection deleted; {output}")


def replay(path):
    report = read_json(path)
    require(report["schema_version"] == 1 and report["task"] == "Stage 4 / Task 5" and report["status"] == "complete" and not report["holdout_queried"], "Completed Task 5 receipt required")
    for ref in [report["reference_tokens"], report["database"], *report["implementations"].values(), *report["snapshots"].values()]:
        require(file_reference(ROOT / ref["path"]) == ref, "Snapshot proof fingerprint changed")
    replay_tokens(ROOT / report["reference_tokens"]["path"])
    tokens = read_json(ROOT / report["reference_tokens"]["path"])
    token_by_id = {r["chunk_id"]: r["tokens"] for r in tokens["chunks"]}
    loaded = {revision: LexicalSnapshot.from_dict(read_json(ROOT / ref["path"]), CodeTokenizer()) for revision, ref in report["snapshots"].items()}
    require(all(snapshot.revision == revision for revision, snapshot in loaded.items()), "Snapshot revision mismatch")
    for phase in report["phases"]:
        scope = SnapshotScope(**phase["scope"])
        require(phase["state"]["scope"] == scope.to_dict(), "Phase state scope mismatch")
        if phase["snapshot"] is not None:
            snapshot = loaded[phase["state"]["active"]]
            require(snapshot.index.scope == scope and phase["state"]["state"] == "ready"
                    and phase["state"]["target_sources"] == snapshot.index.source_sha256
                    and phase["snapshot"] == report["snapshots"][snapshot.revision], "Published phase does not match its intended inventory")
            if phase["name"].startswith("frozen-"):
                require(all(list(words) == token_by_id[chunk.chunk_id] for chunk, words in zip(snapshot.index.chunks, snapshot.tokens, strict=True)), "Frozen tokens changed")
        else:
            require(phase["state"]["state"] == "incomplete" and phase["request_blocked"], "Failed/drifted phase was not blocked")
    names = [phase["name"] for phase in report["phases"]]
    required = ["initial-alpha", "initial-beta", "source-edited-before-reindex", "changed-and-deleted-reindexed", "failed-rebuild-blocked",
                "explicit-full-recovery", "delete-all-reindexed-empty", "external-vector-drift-blocked", "beta-full-recovery"]
    data = read_json(ROOT / tokens["dataset"]["path"])
    expected_names = ["frozen-" + repo for repo in data["repositories"]] + ["existing-production-read-only"] + required
    require(names == expected_names, "Lifecycle phases dropped, duplicated or reordered")
    frozen_tokens = {}
    for phase in report["phases"][:len(data["repositories"])]:
        snapshot = loaded[phase["state"]["active"]]
        frozen_tokens.update({chunk.chunk_id: list(words) for chunk, words in zip(snapshot.index.chunks, snapshot.tokens, strict=True)})
    require(frozen_tokens == token_by_id, "Frozen Chunk/token corpus dropped or changed")
    def snap(name): return loaded[next(p for p in report["phases"] if p["name"] == name)["state"]["active"]]
    require(snap("source-edited-before-reindex").revision == snap("initial-alpha").revision, "Source-only changes leaked into snapshot")
    require({c.source.path for c in snap("changed-and-deleted-reindexed").index.chunks} == {"keep.py"}, "Deleted file survived reindex")
    require(snap("delete-all-reindexed-empty").index.chunks == (), "Empty reindex retained old Chunks")
    failure = next(p for p in report["phases"] if p["name"] == "failed-rebuild-blocked")
    partial = IndexedRepository.from_dict(failure["partial_dense_image"])
    require(len(partial.chunks) == 1 and partial.source_sha256 != failure["state"]["target_sources"], "Partial failure fixture is not partial")
    require(snap("explicit-full-recovery").index.source_sha256 == failure["state"]["target_sources"], "Recovery differs from failed intended manifest")
    backup = ROOT / report["database"]["path"]
    store = SnapshotStore(backup, read_only=True)
    actual = [store.load_ready(SnapshotScope(**s)).snapshot.revision for s in report["final_scopes"]]
    require(actual == report["final_revisions"] and len(actual) == 7, "Final persistent scope inventory changed")
    expected_scopes = [p["scope"] for p in report["phases"] if p["name"].startswith("frozen-") or p["name"] in {
        "existing-production-read-only", "delete-all-reindexed-empty", "beta-full-recovery"}]
    require(report["final_scopes"] == expected_scopes and report["restored_in_separate_process"] is True, "Final restore scopes changed")
    require(file_reference(backup) == report["database"], "Read-only replay modified database bytes")
    require(report["frozen_index_chunks"] == 129 and report["calls"] == {"real_embedding_calls": 0, "generation_calls": 0, "storage_fixture_embedding_calls": 6}, "Snapshot accounting mismatch")
    return {"offline_replay_passed": True, "frozen_chunks": 129, "phases": len(names), "restored_scopes": len(actual), "generation_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", type=Path)
    args = parser.parse_args()
    if args.verify_only: print(replay(args.verify_only.resolve()))
    else:
        args.output = args.output or ROOT / "evaluation/reports" / f"stage4_snapshots_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
        asyncio.run(verify(args))


if __name__ == "__main__": main()
