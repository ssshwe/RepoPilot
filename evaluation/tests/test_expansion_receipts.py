import asyncio
from copy import deepcopy
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"services/api"),str(ROOT)]
from app.retrieval.expanded_retrieval import ExpandedRetrievalResult
from app.retrieval.scope import ScopedSearchRequest, SearchFilters
from app.retrieval.filtered import FilteredBM25Channel
from app.retrieval.exact import ExactCandidateChannel
from app.retrieval.snapshots import SnapshotCoordinator
from app.retrieval.snapshot_store import SnapshotStore
from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4 import evaluate_expansion as module
from evaluation.stage4.state import read_json,write_new

REPORT = ROOT/"evaluation/reports/stage4_expansion_20261101_task11.json"


@pytest.fixture(scope="module")
def context():
    report = read_json(REPORT)
    inputs = module.inputs()
    cases = inputs[3]
    original,old_rerank = read_json(module.HYBRID),read_json(module.RERANK)
    points = {str(p["id"]):p for p in read_json(ROOT/report["index"]["path"])["points"]}
    return report,inputs,original,old_rerank,points


def test_complete_expansion_replays_without_network_or_models(monkeypatch):
    attempts = []
    def deny(*args,**kwargs):
        attempts.append(True)
        raise AssertionError("Offline expansion replay attempted a connection")
    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(socket.socket,"connect",deny)
            patch.setattr(socket,"create_connection",deny)
            return await module.replay(REPORT)
    result = asyncio.run(run())
    assert result["offline_replay_passed"] and result["formal_cases"] == 52 and result["llm_probes"] == 4 and result["live_scopes"] == 3
    assert not attempts and result["answer_generation_calls"] == 0


@pytest.mark.parametrize("change",["query","vector","vector_association","native_filter","rank","matched_queries","original_rerank","context","metrics","diagnosis"])
def test_raw_rows_bind_real_vectors_actual_context_and_original_question(context,change):
    report,inputs,original,old_rerank,points = context
    cases,store,images = inputs[3:]
    ordinal = next(i for i,c in enumerate(cases) if c["id"] == "wallet-symbol")
    row = deepcopy(read_json(ROOT/report["cases"][ordinal]["receipt"]["path"]))
    if change == "query": row["expanded"]["request"]["query"] = "other"
    if change == "vector": next(e for e in row["events"] if e["event"] == "embedding_completed")["vectors"][0][0] += .25
    if change == "vector_association": next(e for e in row["events"] if e["event"] == "embedding_completed")["query"] = "other"
    if change == "native_filter": next(e for e in row["events"] if e["event"] == "qdrant_started")["request"]["query_filter"] = {"must":[]}
    if change == "rank": row["expanded"]["merged"][0]["candidates"][0]["channel_ranks"]["dense"] = 4
    if change == "matched_queries": row["expanded"]["fused"]["candidates"][0]["matched_queries"] = ["invented"]
    if change == "original_rerank": row["expanded"]["reranked"]["reply"]["query_sha256"] = "b"*64
    if change == "context": row["strategies"]["expansion_rerank"]["context"]["sources"] = []
    if change == "metrics": row["strategies"]["expansion"]["metrics"] = {}
    if change == "diagnosis": row["evidence_diagnostics"] = []
    with pytest.raises((ValueError,EvaluationError)):
        module.verify_row(cases[ordinal],row,store.load_ready(images[cases[ordinal]["repository"]].scope).snapshot,points,
            original["cases"][ordinal],old_rerank["cases"][ordinal],report["configuration"])


@pytest.mark.parametrize("change",["default","holdout","inventory","llm_inventory","summary","calls","reference"])
def test_report_scope_quality_denominators_and_costs_are_frozen(context,tmp_path,monkeypatch,change):
    report,inputs,_,_,_ = context
    value = deepcopy(report)
    if change == "default": value["default_expansion_enabled"] = True
    if change == "holdout": value["holdout_queried"] = True
    if change == "inventory": value["cases"].pop()
    if change == "llm_inventory": value["llm_probes"].pop()
    if change == "summary": value["summary"] = {}
    if change == "calls": value["calls"]["new_embedding_calls"] = 0
    if change == "reference": value["definition"]["sha256"] = "b"*64
    path = tmp_path/"changed.json"
    write_new(path,value)
    async def checked(_): return None
    monkeypatch.setattr(module,"replay_rerank",checked)
    monkeypatch.setattr(module,"inputs",lambda:inputs)
    with pytest.raises((ValueError,EvaluationError)):
        asyncio.run(module.replay(path))


@pytest.mark.parametrize("kind",["report","artifacts"])
def test_existing_expansion_outputs_are_rejected_before_model_or_input_work(tmp_path,monkeypatch,kind):
    path = tmp_path/"keep.json"
    if kind == "report": path.write_bytes(b"unchanged")
    else: module.artifacts_for(path).mkdir()
    async def forbidden(*args): raise AssertionError("Input/model work happened before overwrite rejection")
    monkeypatch.setattr(module,"replay_rerank",forbidden)
    with pytest.raises(EvaluationError,match="overwritten"):
        asyncio.run(module.evaluate(path,tmp_path/"missing-python",tmp_path/"missing-model"))
    if kind == "report": assert path.read_bytes() == b"unchanged"


@pytest.mark.parametrize("filters",[{}, {"language":"java","path_prefix":"backend/wallet/"}, {"language":"rust"}])
def test_lexical_checker_recomputes_per_query_ranks_and_filters_without_services(tmp_path,filters):
    from tests.test_hybrid_rrf import IMAGE
    from tests.test_lexical_snapshots import Reader,publish
    async def run():
        store = SnapshotStore(tmp_path/"snapshot.sqlite3")
        snapshot = publish(store,IMAGE)
        coordinator = SnapshotCoordinator(store,Reader(IMAGE))
        request = ScopedSearchRequest("demo","WalletService.credit 在哪里?",30,filters=SearchFilters(**filters))
        for channel in (FilteredBM25Channel(coordinator),ExactCandidateChannel(coordinator)):
            result = await channel.recall(request)
            module.expected_lexical(result,snapshot)
    asyncio.run(run())


def test_optional_llm_and_live_probes_never_replace_formal_cases(context):
    report,inputs,_,_,_ = context
    assert len(report["cases"]) == len(inputs[3]) == 52
    assert len(report["llm_probes"]) == 4 and len(report["live"]["cases"]) == 3
    for case,entry in zip(inputs[3],report["cases"],strict=True):
        assert entry["id"] == case["id"]
        assert len(entry["queries"]) <= 3
        raw = read_json(ROOT/entry["receipt"]["path"])
        assert raw["expanded"]["plan"]["llm_calls"] == 0
        assert raw["expanded"]["reserved_candidate_slots"] <= 210
        result = ExpandedRetrievalResult.from_dict(raw["expanded"])
        assert all(c.channel_scores["rrf"] <= 3/61 for c in result.fused.candidates)
