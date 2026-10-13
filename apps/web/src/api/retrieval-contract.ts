import type { RetrievalResult, RetrievalSearchRequest, RetrievalSearchResponse } from '@/types/retrieval'

export function validateSearch(request: RetrievalSearchRequest): string | null {
  const name = request.repository
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(name) || name.endsWith('.')) {
    return 'Repository 必须是 1–64 位的仓库名，只能包含英文字母、数字、点、下划线和短横线。'
  }
  if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i.test(name.split('.')[0] ?? '')) {
    return '请使用有效的 Repository 名称，不能使用系统保留名称。'
  }
  if (!request.query.trim()) return '请输入非空 Query。'
  if (Array.from(request.query).length > 8192) return 'Query 不能超过 8192 个字符。'
  if (!Number.isInteger(request.top_k) || request.top_k < 1 || request.top_k > 20) {
    return 'TopK 必须是 1–20 之间的整数。'
  }
  return null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isInteger(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= minimum && value <= maximum
}

function chunkingMetadata(item: Record<string, unknown>): Pick<RetrievalResult,
  'symbol' | 'node_type' | 'parent_symbol' | 'chunking_strategy' | 'chunking_version'> {
  const invalid = () => new Error('检索 Chunk 元数据不完整或不一致，请重新索引仓库。')
  const keys = ['symbol', 'node_type', 'parent_symbol', 'chunking_strategy', 'chunking_version']
  const present = keys.filter(key => Object.prototype.hasOwnProperty.call(item, key)).length
  // Accept the complete Stage 2 shape; never hide partially missing AST metadata.
  if (present === 0) return {
    symbol: null, node_type: null, parent_symbol: null,
    chunking_strategy: 'simple', chunking_version: 'line-window-v1',
  }
  if (present !== keys.length) throw invalid()
  const { symbol, node_type, parent_symbol, chunking_strategy, chunking_version } = item
  const name = (value: unknown): value is string | null => value === null
    || (typeof value === 'string' && Boolean(value.trim()) && Array.from(value).length <= 4096
      && !/[\u0000-\u001f\u007f-\u009f]/.test(value))
  if (!name(symbol) || !name(parent_symbol)
    || !(node_type === null || (typeof node_type === 'string' && /^[a-z][a-z0-9_]{0,127}$/.test(node_type)))) throw invalid()
  if (chunking_strategy !== 'simple' && chunking_strategy !== 'ast' && chunking_strategy !== 'fallback') throw invalid()
  const versions = { simple: 'line-window-v1', ast: 'ast-v1', fallback: 'ast-fallback-line-v1' }
  if (chunking_version !== versions[chunking_strategy]) throw invalid()
  if (chunking_strategy === 'ast') {
    if (node_type === null || (parent_symbol !== null
      && (symbol === null || !symbol.startsWith(`${parent_symbol}.`)))) throw invalid()
  } else if (symbol !== null || node_type !== null || parent_symbol !== null) throw invalid()
  return { symbol, node_type, parent_symbol, chunking_strategy, chunking_version }
}

export function parseSearchResponse(
  data: unknown, request: RetrievalSearchRequest,
): RetrievalSearchResponse {
  const invalid = () => new Error('检索响应格式或仓库范围不正确，请检查后端并重新搜索。')
  if (!isRecord(data)
    || data.repository !== request.repository || data.query !== request.query || data.top_k !== request.top_k
    || typeof data.collection !== 'string' || !data.collection
    || typeof data.embedding_model !== 'string' || !data.embedding_model
    || !isInteger(data.embedding_dimension, 1, 65536)
    || typeof data.retrieval_latency_ms !== 'number' || !Number.isFinite(data.retrieval_latency_ms)
    || data.retrieval_latency_ms < 0
    || !Array.isArray(data.results) || data.results.length > request.top_k) throw invalid()
  const seen = new Set<string>()
  const results = data.results.map((item: unknown, index: number) => {
    if (!isRecord(item) || item.rank !== index + 1 || item.repository !== request.repository
      || typeof item.chunk_id !== 'string' || !item.chunk_id || seen.has(item.chunk_id)
      || typeof item.score !== 'number' || !Number.isFinite(item.score)
      || typeof item.path !== 'string' || !item.path
      || typeof item.language !== 'string' || !item.language
      || !isInteger(item.start_line, 1, Number.MAX_SAFE_INTEGER)
      || !isInteger(item.end_line, item.start_line, Number.MAX_SAFE_INTEGER)
      || typeof item.content !== 'string' || !item.content.trim()
      || Array.from(item.content).length > 8192) throw invalid()
    seen.add(item.chunk_id)
    return {
      rank: index + 1, chunk_id: item.chunk_id, repository: request.repository,
      score: item.score, path: item.path, language: item.language,
      start_line: item.start_line, end_line: item.end_line, content: item.content,
      ...chunkingMetadata(item),
    }
  })
  return {
    repository: request.repository, query: request.query, top_k: request.top_k,
    collection: data.collection, embedding_model: data.embedding_model,
    embedding_dimension: data.embedding_dimension,
    retrieval_latency_ms: data.retrieval_latency_ms, results,
  }
}
