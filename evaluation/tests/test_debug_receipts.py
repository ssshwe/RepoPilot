import asyncio
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"services/api"),str(ROOT)]
from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4.state import read_json,write_new,file_reference
from evaluation.stage4.verify_debug import evaluate,replay,verify_case

REPORT=ROOT/"evaluation/reports/stage4_debug_20261031_task9_release.json"


@pytest.fixture(scope="module")
def context():
    report=read_json(REPORT)
    return report,read_json(ROOT/report["index_before"]["path"])


def test_real_debug_receipts_recompute_without_service_calls():
    assert replay(REPORT)=={"offline_replay_passed":True,"real_http_cases":9,"real_query_embeddings":4,"generation_calls":0}


@pytest.mark.parametrize("change",["query_vector","vector_association","rrf_score","source","score_type","call_count","request_mode"])
def test_actual_debug_vectors_sources_and_fusion_are_bound(context,change):
    report,state=context;row=deepcopy(next(r for r in report["cases"] if r["id"]=="hybrid"))
    if change=="query_vector":next(e for e in row["events"] if e["event"]=="embedding_completed")["vectors"][0][0]+=.25
    elif change=="vector_association":next(e for e in row["events"] if e["event"]=="embedding_started")["text_sha256"]=["0"*64]
    elif change=="rrf_score":row["response"]["fused"]["candidates"][0]["channel_scores"]["rrf"]+=.1
    elif change=="source":row["response"]["channels"]["dense"]["candidates"][0]["source_metadata"]["path"]="forged.java"
    elif change=="score_type":row["response"]["score_type"]="cosine_similarity"
    elif change=="call_count":row["events"].pop()
    else:row["request"]["mode"]="dense"
    with pytest.raises((EvaluationError,ValueError)):verify_case(row,state,report["configuration"])


@pytest.mark.parametrize("change",["implementation","version","missing_case"])
def test_debug_report_identity_and_case_inventory_are_fixed(context,tmp_path,change):
    report=deepcopy(context[0])
    if change=="implementation":report["implementations"]["services/api/app/retrieval/debug.py"]=file_reference(ROOT/"services/api/app/retrieval/service.py")
    elif change=="version":report["schema_version"]=99
    else:report["cases"].pop()
    path=tmp_path/"changed.json";write_new(path,report)
    with pytest.raises(EvaluationError):replay(path)


def test_existing_debug_report_refused_before_provider_or_service_work(tmp_path):
    path=tmp_path/"existing.json";path.write_bytes(b"keep")
    with pytest.raises(EvaluationError,match="overwritten"):asyncio.run(evaluate(path))
    assert path.read_bytes()==b"keep"


def test_controlled_failure_cannot_hide_embedding_or_generation_calls(context):
    report,state=context;row=deepcopy(next(r for r in report["cases"] if r["id"]=="future_feature"))
    row["events"]=[{"event":"generation_attempted"}]
    with pytest.raises(EvaluationError):verify_case(row,state,report["configuration"])


def test_independent_embedding_scores_are_checked_against_their_own_vectors(context):
    report,state=context;row=deepcopy(next(r for r in report["cases"] if r["id"]=="legacy"))
    dense=next(r for r in report["cases"] if r["id"]=="dense")
    assert row["response"]["results"][0]["score"]!=dense["response"]["results"][0]["score"]
    row["response"]["results"][0]["score"]=dense["response"]["results"][0]["score"]
    with pytest.raises(EvaluationError):verify_case(row,state,report["configuration"])


def test_renderer_metadata_fix_reuses_all_original_http_receipts(context):
    report=context[0];execution=report["case_execution"]
    original=read_json(ROOT/execution["original_report"]["path"])
    assert original["cases"]==report["cases"] and execution["new_query_embeddings"]==0
    assert len(execution["source_receipts"])==9 and len(execution["ui_before"])==2


@pytest.mark.parametrize("change",["ui_before","source_receipts"])
def test_renderer_rebinding_cannot_conceal_different_inputs(context,tmp_path,change):
    report=deepcopy(context[0])
    if change=="ui_before":next(iter(report["case_execution"]["ui_before"].values()))["sha256"]="0"*64
    else:report["case_execution"]["source_receipts"].pop()
    path=tmp_path/"changed.json";write_new(path,report)
    with pytest.raises(EvaluationError):replay(path)
