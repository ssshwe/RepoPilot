"""Offline proof integrity; these synthetic inputs are not model-quality evidence."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/api"))
sys.path.insert(0, str(ROOT))
from app.context import ContextBuilder
from app.retrieval.candidates import CandidateBatch, RetrievalCandidate, SourceMetadata, to_retrieval_results
from evaluation.retrieval.evaluate import EvaluationError
from evaluation.stage4.metrics import case_metrics
from evaluation.stage4.state import fingerprint
from evaluation.stage4.verify_candidates import verify, verify_row, summary


def fixture():
    case = {"id": "fixture", "repository": "demo", "query": "退款", "split": "dev", "category": "symbol",
            "expected_paths": ["a.py"], "expected_symbols": [], "evidence": [{"path": "a.py", "start_line": 1, "end_line": 1}]}
    points = []
    for rank, vector in enumerate(([.8, .6, 0.], [.6, .8, 0.]), 1):
        identity = str(uuid4())
        source = SourceMetadata("a.py", "python", rank, rank, f"call_{rank}()\n")
        points.append({"id": identity, "payload": source.to_dict() | {"chunk_id": identity, "repository": "demo"}, "vector": vector})
    snapshot = {"config": {"params": {"vectors": {"size": 3}}}, "points": points}
    revision = "sha256:" + fingerprint(snapshot)
    candidates = [RetrievalCandidate(p["id"], "demo", revision, SourceMetadata.from_payload(p["payload"]),
                                     {"dense": p["vector"][0]}, {"dense": rank}, (case["query"],)) for rank, p in enumerate(points, 1)]
    batch = CandidateBatch("demo", case["query"], "dense", revision, 30, tuple(candidates), 650.)
    config = {"embedding_model": "test", "max_context_chunks": 5, "max_context_characters": 16000}
    results = to_retrieval_results(candidates, repository="demo", index_revision=revision)
    built = ContextBuilder(max_context_chunks=5, max_context_characters=16000).build("demo", results)
    legacy = {str(k): {"repository": "demo", "query": case["query"], "top_k": k, "collection": "temp",
                      "embedding_model": "test", "embedding_dimension": 3, "retrieval_latency_ms": 1.,
                      "results": [r.model_dump() for r in results[:k]]} for k in (1, 5, 20)}
    row = {"id": case["id"], "batch": batch.to_dict(), "query_vector": [1., 0., 0.], "legacy": legacy,
           "context": json.loads(json.dumps(asdict(built))), "metrics": case_metrics(case, [r.model_dump() for r in results], [asdict(s) for s in built.sources])}
    return case, row, snapshot, config


def test_proof_recomputes_cosine_context_metrics_and_uses_uncached_latency():
    case, row, snapshot, config = fixture()
    verify_row(case, row, snapshot, config)
    assert summary([row], [case])["metrics"]["split"]["dev"]["latency_ms"]["p50"] == 650.


@pytest.mark.parametrize("change", ["score", "vector", "source", "legacy", "context", "metrics", "revision", "missing_comparison"])
def test_forged_candidate_proof_is_rejected(change):
    case, row, snapshot, config = fixture()
    row = deepcopy(row)
    if change == "score":
        row["batch"]["candidates"][0]["channel_scores"]["dense"] = .9
    elif change == "vector":
        row["query_vector"] = [0., 1., 0.]
    elif change == "source":
        row["batch"]["candidates"][0]["source_metadata"]["content"] = "forged()\n"
    elif change == "legacy":
        row["legacy"]["5"]["results"] = []
    elif change == "context":
        row["context"]["sources"] = []
    elif change == "metrics":
        row["metrics"] = {}
    elif change == "revision":
        row["batch"]["index_revision"] = "sha256:" + "b" * 64
    else:
        del row["legacy"]["20"]
    with pytest.raises((EvaluationError, ValueError)):
        verify_row(case, row, snapshot, config)


def test_report_overwrite_is_refused_before_any_provider_work(tmp_path):
    output = tmp_path / "proof.json"
    output.write_bytes(b"existing")
    with pytest.raises(EvaluationError, match="overwritten"):
        asyncio.run(verify(SimpleNamespace(output=output)))
    assert output.read_bytes() == b"existing"
