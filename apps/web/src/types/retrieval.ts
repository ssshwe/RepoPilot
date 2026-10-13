export interface RetrievalSearchRequest {
  repository: string
  query: string
  top_k: number
}

export interface RetrievalResult {
  rank: number
  chunk_id: string
  repository: string
  score: number
  path: string
  language: string
  start_line: number
  end_line: number
  content: string
  symbol: string | null
  node_type: string | null
  parent_symbol: string | null
  chunking_strategy: 'simple' | 'ast' | 'fallback'
  chunking_version: string
}

export interface RetrievalSearchResponse {
  repository: string
  query: string
  top_k: number
  collection: string
  embedding_model: string
  embedding_dimension: number
  retrieval_latency_ms: number
  results: RetrievalResult[]
}
