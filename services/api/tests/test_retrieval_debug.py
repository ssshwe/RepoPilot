import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest
from qdrant_client import AsyncQdrantClient, models

from app.api import retrieval_debug as api
from app.core.config import Settings
from app.embedding.identity import embedding_identity
from app.main import app
from app.retrieval.snapshot_models import SnapshotScope
from app.retrieval.vector_store import QdrantVectorStore
from app.schemas.retrieval_debug import DebugSearchResponse
from tests.test_exact_and_filters import records, repo_image


class Provider:
    model_name="test"
    def __init__(self):self.calls=[];self.closed=0
    async def embed_texts(self,texts):self.calls.append(texts);return [[1.,0.,0.]]
    async def aclose(self):self.closed+=1


@pytest.fixture
def runtime(tmp_path,monkeypatch):
    settings=Settings(_env_file=None,embedding_model="test",embedding_base_url="http://localhost:11434/v1",qdrant_collection="task9_code")
    client=AsyncQdrantClient(":memory:");store=QdrantVectorStore(client,settings.qdrant_collection)
    async def seed():
        await store.prepare_index(3,embedding_identity(settings,"test"))
        raw=records("demo",[c.source for c in repo_image().chunks])
        for point in raw:point["payload"]["private_payload"]="DO_NOT_EXPORT_TEST_MARKER"
        raw+=records("other",[repo_image().chunks[0].source])
        await store.upsert_points([models.PointStruct(**p) for p in raw])
        await store.record_repository_chunking("demo",dict(repo_image().configuration["chunking"]))
    asyncio.run(seed())
    real_close=client.close;client.close=AsyncMock()
    providers=[]
    def create(_):
        p=Provider();providers.append(p);return p
    monkeypatch.setattr(api,"get_settings",lambda:settings)
    monkeypatch.setattr(api,"create_qdrant_client",lambda _:client)
    monkeypatch.setattr(api,"SNAPSHOT_PATH",tmp_path/"snapshots.db")
    monkeypatch.setattr(api.EmbeddingProviderFactory,"create",create)
    with TestClient(app) as http:yield http,client,settings,providers
    asyncio.run(real_close())


def request(mode="hybrid",**kwargs):
    return {"repository":"demo","query":"WalletService.credit","top_k":5,"mode":mode,
        "filters":{"language":None,"path_prefix":None},"rerank":False,"query_expansion":False}|kwargs


def sync(http):
    response=http.post("/api/retrieval/debug/snapshot",json={"repository":"demo"})
    assert response.status_code==200,response.text
    return response.json()


@pytest.mark.parametrize("mode,score_type",[("dense","cosine_similarity"),("bm25","bm25"),("hybrid","rrf")])
def test_debug_modes_preserve_real_point_sources_provenance_and_resources(runtime,mode,score_type):
    http,client,_,providers=runtime
    before=asyncio.run(client.scroll("task9_code",with_payload=True,with_vectors=True))[0]
    snapshot=sync(http);response=http.post("/api/retrieval/debug",json=request(mode))
    assert response.status_code==200,response.text
    body=response.json();assert body["score_type"]==score_type and body["index_revision"]==snapshot["index_revision"]
    assert len(body["results"])==3 and all(c["repository"]=="demo" for c in body["results"])
    assert "DO_NOT_EXPORT_TEST_MARKER" not in response.text
    assert len(providers)==(0 if mode=="bm25" else 1)
    assert all(p.calls==[["WalletService.credit"]] and p.closed==1 for p in providers)
    assert client.close.await_count==2
    assert [p.model_dump() for p in asyncio.run(client.scroll("task9_code",with_payload=True,with_vectors=True))[0]]==[p.model_dump() for p in before]
    assert DebugSearchResponse.model_validate(body).model_dump()==body


@pytest.mark.parametrize("mode",["dense","bm25","hybrid"])
def test_debug_filters_apply_before_limit_and_zero_scope_does_not_embed(runtime,mode):
    http,_,_,providers=runtime;sync(http)
    response=http.post("/api/retrieval/debug",json=request(mode,filters={"language":"java","path_prefix":"backend/wallet/"},top_k=1))
    assert response.status_code==200,response.text
    assert len(response.json()["results"])==1 and response.json()["results"][0]["path"]=="backend/wallet/WalletService.java"
    calls=sum(len(p.calls) for p in providers)
    response=http.post("/api/retrieval/debug",json=request(mode,filters={"language":"rust","path_prefix":None}))
    assert response.status_code==200 and response.json()["results"]==[]
    assert sum(len(p.calls) for p in providers)==calls


def test_missing_snapshot_fails_without_provider_and_explicit_sync_recovers(runtime):
    http,_,_,providers=runtime
    assert http.post("/api/retrieval/debug",json=request()).status_code==409
    assert providers==[]
    assert sync(http)["chunks"]==3 and providers==[]
    assert http.post("/api/retrieval/debug",json=request("bm25")).status_code==200 and providers==[]


def test_index_vector_drift_is_discarded_until_explicit_snapshot_sync(runtime):
    http,client,_,providers=runtime;before=sync(http)
    point=asyncio.run(client.scroll("task9_code",with_payload=True,with_vectors=True))[0][0]
    asyncio.run(client.upsert("task9_code",[models.PointStruct(id=point.id,vector=[0.,1.,0.],payload=point.payload)],wait=True))
    assert http.post("/api/retrieval/debug",json=request()).status_code==409
    assert all(p.closed==1 for p in providers)
    assert sync(http)["index_revision"]!=before["index_revision"]
    assert http.post("/api/retrieval/debug",json=request("bm25")).status_code==200


@pytest.mark.parametrize("changes",[{"rerank":True},{"query_expansion":True},{"mode":"exact"},{"top_k":21},
    {"filters":{"path_prefix":"../"}},{"filters":{"language":"JAVA"}},{"repository":"../demo"},{"query":"\ud800"}])
def test_invalid_debug_requests_are_safe_before_client_work(runtime,changes):
    http,client,_,providers=runtime
    response=http.post("/api/retrieval/debug",content=__import__('json').dumps(request(**changes)),headers={"Content-Type":"application/json"})
    assert response.status_code==422 and response.json()=={"detail":"Invalid retrieval debug request."}
    assert not providers and client.close.await_count==0


@pytest.mark.parametrize("change",["score_type","revision","scope","rrf_score","raw_rank","contribution","context_coordinate","timing"])
def test_debug_response_rejects_mixed_or_modified_pipeline_evidence(runtime,change):
    http,_,_,_=runtime;sync(http)
    body=http.post("/api/retrieval/debug",json=request()).json()
    if change=="score_type":body["score_type"]="cosine_similarity"
    elif change=="revision":body["channels"]["bm25"]["index_revision"]="sha256:"+"b"*64
    elif change=="scope":body["channels"]["bm25"]["filters"]["language"]="rust"
    elif change=="rrf_score":body["fused"]["candidates"][0]["channel_scores"]["rrf"]+=.1
    elif change=="raw_rank":body["fused"]["candidates"][0]["channel_ranks"]["dense"]=60
    elif change=="contribution":next(iter(next(iter(body["rrf_contributions"].values())).values()))["contribution"]+=.1
    elif change=="context_coordinate":body["results"][0]["start_line"]+=1
    else:body["timings"]["total_ms"]+=100
    with pytest.raises(ValueError):DebugSearchResponse.model_validate(deepcopy(body))


def test_debug_known_nonready_state_keeps_provider_unresolved(runtime):
    http,client,settings,providers=runtime;sync(http)
    coordinator=api.create_coordinator(settings,client)
    scope=coordinator.reader.scope("demo")
    source=coordinator.store.load_ready(scope).snapshot.index.source_sha256
    with coordinator.store.rebuilding(scope,source):
        assert http.post("/api/retrieval/debug",json=request()).status_code==409
        assert providers==[]
