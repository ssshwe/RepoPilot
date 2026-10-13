export interface CodeChatRequest { repository: string; question: string; top_k: number }
export interface CodeChatCitation {
  repository: string; source_id: string; path: string; symbol: string | null
  start_line: number; end_line: number
}
export interface CodeChatSource { source_id: string; language: string; content: string }
export interface CodeChatResponse {
  repository: string; question: string; answer: string; source_ids: string[]
  citations: CodeChatCitation[]; sources: CodeChatSource[]
  status: 'answered' | 'insufficient_evidence'; model: string | null
  retrieval: {
    top_k: number; retrieved_chunks: number; used_sources: number; context_characters: number
    context_budget_characters: number; budget_limited: boolean
    empty_reason: 'no_results' | 'budget_too_small' | null
    collection: string; embedding_model: string; embedding_dimension: number; latency_ms: number
  }
  generation: { called: boolean; latency_ms: number }
}
export interface CodeChatTurn {
  id: number; request: CodeChatRequest; state: 'pending' | 'complete' | 'error' | 'cancelled'
  response: CodeChatResponse | null; error: string | null
}
