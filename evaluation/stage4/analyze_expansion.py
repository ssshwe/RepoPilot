"""Offline expansion gains/losses and actual per-query pipeline costs."""
import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/"services/api"),str(ROOT)]
from evaluation.retrieval.evaluate import require
from evaluation.stage4.metrics import percentile
from evaluation.stage4.state import file_reference,read_json,write_new


def analyze(path):
    report = read_json(path)
    require(report["task"] == "Stage 4 / Task 11" and report["status"] == "complete", "Complete expansion report required")
    definition = read_json(ROOT/report["definition"]["path"])
    original = read_json(ROOT/definition["hybrid"]["path"])
    rerank = read_json(ROOT/definition["rerank"]["path"])
    require(file_reference(ROOT/definition["hybrid"]["path"]) == definition["hybrid"]
            and file_reference(ROOT/definition["rerank"]["path"]) == definition["rerank"],"Expansion baseline drift")
    costs,changes,diagnostics,llm,rank_changes = [],[],[],[],[]
    for entry,old,old_rerank in zip(report["cases"],original["cases"],rerank["cases"],strict=True):
        ref = entry["receipt"]
        require(file_reference(ROOT/ref["path"]) == ref,"Expansion raw receipt drift")
        row = read_json(ROOT/ref["path"])
        expanded = row["expanded"]
        costs.append({"id":entry["id"],"planned_queries":len(expanded["plan"]["queries"]),"used_queries":len(expanded["used_queries"]),
            "pipeline_wall_ms":expanded["retrieval_latency_ms"],"expansion_ms":expanded["plan"]["latency_ms"],"rerank_stage_ms":expanded["reranked"]["latency_ms"],
            "new_embedding_calls":sum(e["event"] == "embedding_started" for e in row["events"]),
            "reserved_candidate_slots":expanded["reserved_candidate_slots"],"returned_candidate_slots":expanded["returned_candidate_slots"],
            "fused_candidates":len(expanded["fused"]["candidates"]),"variant_failure":expanded["reason"],
            "rerank_status":expanded["reranked"]["status"]})
        for mode in ("fusion","rerank"):
            comparison = row["comparisons"][mode]
            if not comparison["positive"]: continue
            delta = {n:v["after"]-v["before"] for n,v in comparison["at_5"].items() if v["before"] is not None}
            if any(v != 0 for v in delta.values()):
                before_results = old["strategies"]["hybrid"]["retrieval"]["results"] if mode == "fusion" else old_rerank["measured"]["retrieval"]["results"]
                after_results = row["strategies"]["expansion" if mode == "fusion" else "expansion_rerank"]["retrieval"]["results"]
                compact = lambda results:[{k:r[k] for k in ("rank","chunk_id","path","start_line","end_line")} for r in results]
                changes.append({"id":entry["id"],"mode":mode,"delta_at_5":delta,"before":compact(before_results),"after":compact(after_results),
                    "actual_queries":expanded["used_queries"],"receipt":ref})
        diagnostics += [{"id":entry["id"],**d} for d in row["evidence_diagnostics"] if d["stage"] != "covered"]
        before_ranks = {c["chunk_id"]:c["channel_ranks"]["rrf"] for c in old["hybrid"]["fused"]["candidates"]}
        for evidence in row["evidence_diagnostics"]:
            for chunk in evidence["chunks"]:
                before_rank = before_ranks.get(chunk["chunk_id"])
                if before_rank is not None and before_rank != chunk["fused_rank"]:
                    rank_changes.append({"id":entry["id"],"required_range":evidence["required_range"],"chunk":chunk,
                        "before_fused_rank":before_rank,"after_fused_rank":chunk["fused_rank"],"receipt":ref})
    for entry in report["llm_probes"]:
        require(file_reference(ROOT/entry["receipt"]["path"]) == entry["receipt"],"LLM probe drift")
        row = read_json(ROOT/entry["receipt"]["path"])
        plan = row["expanded"]["plan"]
        end = next((e for e in row["events"] if e["event"] in {"llm_completed","llm_interrupted"}),None)
        receipt = end["receipt"] if end else None
        llm.append({"id":entry["id"],"status":plan["status"],"reason":plan["reason"],"queries":row["expanded"]["used_queries"],
            "rejections":plan["rejections"],"http_attempts":receipt["http_attempts"] if receipt else None,
            "usage":receipt["usage"] if receipt else None,"finish_reason":receipt["finish_reason"] if receipt else None,
            "raw_output_characters":len(receipt["raw_content"]) if receipt and receipt["raw_content"] else 0,
            "expansion_ms":plan["latency_ms"],"pipeline_wall_ms":row["expanded"]["retrieval_latency_ms"],
            "comparison":row["comparisons"],"receipt":entry["receipt"]})
    def distribution(values):
        return {name:percentile(values,p) for name,p in (("min",0),("p50",.5),("p95",.95),("max",1))}
    return {"report":file_reference(path),"quality_summaries":report["summary"],"before_summaries":report["before_summary"],
        "cost_definition":"Actual expanded.retrieval_latency_ms per request, including original rank reuse, frozen guards, new variant embedding/search and rerank; strategy helper fusion latency is a zero placeholder and its rerank latency is stage-only; those strategy latency summary fields are not end-to-end costs",
        "formal_costs":costs,"formal_pipeline_wall_ms":distribution([c["pipeline_wall_ms"] for c in costs]),
        "formal_nonempty_pipeline_wall_ms":distribution([c["pipeline_wall_ms"] for c in costs if c["rerank_status"] == "reranked"]),
        "formal_new_embedding_calls":sum(c["new_embedding_calls"] for c in costs),
        "query_counts":dict(sorted(Counter(str(c["used_queries"]) for c in costs).items())),
        "changed_cases":changes,"annotated_evidence_rank_changes":rank_changes,
        "unresolved_evidence_ranges":diagnostics,"unresolved_stage_counts":dict(Counter(d["stage"] for d in diagnostics)),
        "llm_probes":llm,"calls":report["calls"],"formal_denominator":52,"llm_probe_denominator":4,"live_scope_denominator":3}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--verify-only",action="store_true")
    args = parser.parse_args()
    value = analyze(args.report.resolve())
    if args.verify_only:
        require(read_json(args.output) == value,"Derived expansion quality/cost table differs")
        print("Expansion analysis recomputed offline")
    else:
        write_new(args.output,value)
        print("Saved actual pipeline costs and all gains/losses: "+str(args.output))


if __name__ == "__main__": main()
