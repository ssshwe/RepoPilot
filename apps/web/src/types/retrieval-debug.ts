import type { RetrievalResult, RetrievalSearchRequest, RetrievalSearchResponse } from './retrieval'

export type DebugMode = 'dense' | 'bm25' | 'hybrid'
export type DebugChannel = 'dense' | 'bm25' | 'exact' | 'rrf'
export interface DebugFilters { language: string | null; path_prefix: string | null }
export interface DebugRequest extends RetrievalSearchRequest {
  mode: DebugMode; filters: DebugFilters; rerank: false; query_expansion: false
}
export interface DebugCandidate {
  chunk_id: string; repository: string; index_revision: string
  source_metadata: Omit<RetrievalResult, 'rank' | 'chunk_id' | 'repository' | 'score'>
  channel_scores: Record<string, number>; channel_ranks: Record<string, number>; matched_queries: string[]
}
export interface MatchReason {
  kind: string; strength: 'full' | 'partial'; location: string; matched_value: string
  start_line: number; end_line: number; char_start: number | null; char_end_exclusive: number | null; occurrences: number
}
export interface DebugBatch {
  repository: string; query: string; channel: DebugChannel; index_revision: string; candidate_limit: number
  retrieval_latency_ms: number; filters: DebugFilters; scope_fingerprint: string; candidates: DebugCandidate[]
  match?: { kind: string; value: string; mode: 'full' | 'partial' } | null
  reasons?: Record<string, MatchReason>
}
export interface DebugResponse extends RetrievalSearchResponse {
  mode: DebugMode; filters: DebugFilters; index_revision: string; score_type: 'cosine_similarity' | 'bm25' | 'rrf'
  limits: { dense: number; bm25: number; exact: number; fusion: number; rerank: number; top_k: number }
  channels: Partial<Record<DebugChannel, DebugBatch>>; fused: DebugBatch | null; rrf_k: number | null
  untruncated_union_count: number; rerank: false; query_expansion: false
  rrf_contributions: Record<string, Record<string, { rank: number; raw_score: number; contribution: number }>>
  timings: { channel_ms: Record<string, number>; fusion_ms: number; overhead_ms: number; total_ms: number }
}
export interface SnapshotResponse { repository: string; collection: string; index_revision: string; chunks: number; elapsed_ms: number }
