import { validateSearch } from '@/api/retrieval-contract'
import type { CodeChatRequest, CodeChatResponse } from '@/types/code-chat'

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function integer(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= min && value <= max
}
function text(value: unknown, max: number): value is string {
  return typeof value === 'string' && Boolean(value.trim()) && Array.from(value).length <= max
    && !Array.from(value).some(c => { const n = c.codePointAt(0)!; return n >= 0xd800 && n <= 0xdfff })
}
function latency(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}
export function sourceLines(content: string): string[] {
  const lines = content.split(/\r\n|[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/)
  if (lines[lines.length - 1] === '') lines.pop()
  return lines
}
export function validateCodeChat(request: CodeChatRequest): string | null {
  const failure = validateSearch({ repository: request.repository, query: request.question, top_k: request.top_k })
  if (failure) return failure.replace(/Query/g, '问题')
  if (!text(request.question, 8192)) return '问题包含无效字符，请重新输入。'
  return null
}

export function codeChatHttpError(status: number, detail?: unknown): string {
  if (status === 502 && detail === 'Code chat generation returned potentially sensitive output.') {
    return '回答可能包含敏感信息，已被阻止。本次没有返回答案或引用。'
  }
  switch (status) {
    case 404: return '索引集合不存在，请先为仓库建立索引。'
    case 409: return '模型与索引不兼容，请检查索引配置。'
    case 422: return '问答参数无效，请检查仓库、问题和 TopK。'
    case 502: return '回答生成或校验失败，本次没有可用答案。请重试或换个问题。'
    case 503: return '问答服务不可用，请检查模型服务和 Qdrant 后重试。'
    default: return `问答失败（HTTP ${status}），请稍后重试。`
  }
}

export function parseCodeChatResponse(data: unknown, request: CodeChatRequest): CodeChatResponse {
  const invalid = () => new Error('问答响应或引用范围不正确，未展示本次答案。请检查后端后重试。')
  if (!record(data) || data.repository !== request.repository || data.question !== request.question
    || !text(data.answer, 8192) || !(data.model === null || text(data.model, 4096))
    || !Array.isArray(data.source_ids) || !Array.isArray(data.citations) || !Array.isArray(data.sources)
    || data.source_ids.length > 20 || data.citations.length !== data.source_ids.length
    || data.sources.length !== data.source_ids.length || new Set(data.source_ids).size !== data.source_ids.length
    || !record(data.retrieval) || !record(data.generation)) throw invalid()
  const r = data.retrieval, g = data.generation
  if (r.top_k !== request.top_k || !integer(r.retrieved_chunks, 0, request.top_k)
    || !integer(r.used_sources, 0, 20) || !integer(r.context_budget_characters, 1, 200000)
    || !integer(r.context_characters, 0, r.context_budget_characters) || typeof r.budget_limited !== 'boolean'
    || ![null, 'no_results', 'budget_too_small'].includes(r.empty_reason as null)
    || !text(r.collection, 4096) || !text(r.embedding_model, 4096) || !integer(r.embedding_dimension, 1, 65536)
    || !latency(r.latency_ms) || typeof g.called !== 'boolean' || !latency(g.latency_ms)
    || (data.status !== 'answered' && data.status !== 'insufficient_evidence')
    || (data.status === 'answered') !== Boolean(data.source_ids.length)
    || (r.used_sources === 0) !== (r.context_characters === 0)
    || (r.used_sources === 0) !== (r.empty_reason !== null)
    || (r.empty_reason === 'no_results' && r.retrieved_chunks !== 0)
    || (r.empty_reason === 'budget_too_small' && r.retrieved_chunks === 0)
    || (!g.called && (data.model !== null || data.source_ids.length !== 0 || g.latency_ms !== 0 || r.used_sources !== 0))
    || (g.called && (data.model === null || r.used_sources === 0))) throw invalid()
  const ids = data.source_ids, usedSources = r.used_sources
  const citations = data.citations.map((c: unknown, i: number) => {
    const id = ids[i]
    if (typeof id !== 'string' || !/^S(?:[1-9]|1[0-9]|20)$/.test(id) || Number(id.slice(1)) > usedSources
      || !record(c) || c.source_id !== id || c.repository !== request.repository
      || !text(c.path, 4096) || /[\\:\u0000-\u001f\u007f-\u009f]/.test(c.path)
      || c.path.split('/').some(p => !p || p === '.' || p === '..')
      || !(c.symbol === null || (text(c.symbol, 4096) && !/[\u0000-\u001f\u007f-\u009f]/.test(c.symbol)))
      || !integer(c.start_line, 1, Number.MAX_SAFE_INTEGER) || !integer(c.end_line, c.start_line, Number.MAX_SAFE_INTEGER)) throw invalid()
    return { source_id: id, repository: request.repository, path: c.path, symbol: c.symbol,
      start_line: c.start_line, end_line: c.end_line }
  })
  const sources = data.sources.map((s: unknown, i: number) => {
    if (!record(s) || s.source_id !== citations[i]!.source_id || !text(s.language, 32) || !text(s.content, 8192)
      || sourceLines(s.content).length !== citations[i]!.end_line - citations[i]!.start_line + 1) throw invalid()
    return { source_id: citations[i]!.source_id, language: s.language, content: s.content }
  })
  return {
    repository: request.repository, question: request.question, answer: data.answer,
    source_ids: citations.map(c => c.source_id), citations, sources, status: data.status, model: data.model,
    retrieval: { top_k: request.top_k, retrieved_chunks: r.retrieved_chunks, used_sources: r.used_sources,
      context_characters: r.context_characters, context_budget_characters: r.context_budget_characters,
      budget_limited: r.budget_limited, empty_reason: r.empty_reason as 'no_results' | 'budget_too_small' | null,
      collection: r.collection, embedding_model: r.embedding_model, embedding_dimension: r.embedding_dimension, latency_ms: r.latency_ms },
    generation: { called: g.called, latency_ms: g.latency_ms },
  }
}
