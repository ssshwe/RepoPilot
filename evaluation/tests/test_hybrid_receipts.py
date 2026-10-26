import asyncio
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"services/api"),str(ROOT)]
from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4.evaluate_hybrid import checked_inputs,check_case,evaluate,replay,verify_live
from evaluation.stage4.state import file_reference,read_json,write_new

REPORT=ROOT/"evaluation/reports/stage4_hybrid_20261027_task8.json"


@pytest.fixture(scope="module")
def context():
    dense,archive,bm25,cases,store,images=checked_inputs()
    return read_json(REPORT),dense,archive,bm25,cases,store,images


def test_full_hybrid_comparison_recomputes_without_models():
    assert asyncio.run(replay(REPORT))=={"offline_replay_passed":True,"cases":52,"strategies":4,
        "live_scenarios":3,"real_embedding_calls":0,"generation_calls":0}


@pytest.mark.parametrize("change",["rrf_score","dense_raw_score","contribution","scope","context","metrics","delta","exact_dropped"])
def test_hybrid_case_provenance_context_and_metrics_are_bound(context,change):
    report,dense,archive,bm25,cases,store,images=context
    case=next(c for c in cases if c["id"]=="wallet-symbol")
    row=deepcopy(next(r for r in report["cases"] if r["id"]==case["id"]))
    if change=="rrf_score":row["hybrid"]["fused"]["candidates"][0]["channel_scores"]["rrf"]+=.01
    elif change=="dense_raw_score":row["hybrid"]["channels"]["dense"]["candidates"][0]["channel_scores"]["dense"]+=.1
    elif change=="contribution":next(iter(next(iter(row["hybrid"]["rrf_contributions"].values())).values()))["contribution"]+=.01
    elif change=="scope":row["hybrid"]["request"]["filters"]["language"]="python"
    elif change=="context":row["strategies"]["hybrid"]["context"]["sources"]=[]
    elif change=="metrics":row["strategies"]["hybrid"]["metrics"]["at_k"]["5"]["range_recall"]=-1
    elif change=="delta":row["deltas_at_5"]["dense"]["range_recall"]=-1
    else:row["hybrid"]["channels"].pop("exact")
    with pytest.raises((ValueError,EvaluationError)):
        check_case(case,row,store.load_ready(images[case["repository"]].scope).snapshot,
            next(r for r in archive["cases"] if r["id"]==case["id"]),next(r for r in bm25["cases"] if r["id"]==case["id"]),
            next(r for r in dense["cases"] if r["id"]==case["id"]),report["configuration"])


@pytest.mark.parametrize("change",["association","implementation","k","limits","missing_case"])
def test_hybrid_report_inputs_parameters_and_case_inventory_cannot_be_relabelled(context,tmp_path,change):
    report=deepcopy(context[0])
    if change=="association":report["dense_reference"]=file_reference(REPORT)
    elif change=="implementation":report["implementations"]["services/api/app/retrieval/rrf.py"]=file_reference(ROOT/"services/api/app/retrieval/bm25.py")
    elif change=="k":report["parameters"]["k"]=10
    elif change=="limits":report["limits"]["fusion"]=30
    else:report["cases"].pop()
    path=tmp_path/"changed.json";write_new(path,report)
    with pytest.raises(EvaluationError):asyncio.run(replay(path))


@pytest.mark.parametrize("change",["native_filter","rrf_score","cleanup"])
def test_real_hybrid_scope_rank_and_cleanup_evidence_are_bound(context,change):
    report,_,archive,_,_,_,_=context
    live=deepcopy(read_json(ROOT/report["live"]["path"]))
    row=next(r for r in live["rows"] if r["id"]=="formal")
    if change=="native_filter":row["native_dense_query"][0]["filter"]["must"].pop()
    elif change=="rrf_score":row["hybrid"]["fused"]["candidates"][0]["channel_scores"]["rrf"]+=.01
    else:live["temporary_collection_deleted"]=False
    with pytest.raises((ValueError,EvaluationError)):verify_live(live,archive)


def test_existing_hybrid_output_refused_before_any_provider_work(tmp_path):
    path=tmp_path/"existing.json";path.write_bytes(b"keep")
    with pytest.raises(EvaluationError,match="overwritten"):asyncio.run(evaluate(SimpleNamespace(output=path)))
    assert path.read_bytes()==b"keep"
