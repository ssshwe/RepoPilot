import { parseSearchResponse, validateSearch } from '@/api/retrieval-contract'
import type { DebugBatch, DebugCandidate, DebugChannel, DebugRequest, DebugResponse, MatchReason, SnapshotResponse } from '@/types/retrieval-debug'

const channels = ['dense', 'bm25', 'exact', 'rrf'] as const
const kinds = ['symbol', 'short_symbol', 'path', 'filename', 'text', 'literal']
const invalid = () => new Error('检索诊断的范围、坐标或排名不一致，请检查后端后重新搜索。')
function check(value: unknown): asserts value { if (!value) throw invalid() }
function record(value: unknown): Record<string, unknown> { check(value !== null && typeof value === 'object' && !Array.isArray(value)); return value as Record<string, unknown> }
function finite(value: unknown, min = -Infinity): number { check(typeof value === 'number' && Number.isFinite(value) && value >= min); return value }
function integer(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER): number { const n = finite(value, min); check(Number.isInteger(n) && n <= max); return n }
function keys(value: object, expected: string[]): void { check(Object.keys(value).sort().join('|') === [...expected].sort().join('|')) }
function revision(value: unknown): string { check(typeof value === 'string' && /^sha256:[a-f0-9]{64}$/.test(value)); return value }
function close(a: number, b: number): boolean { return Math.abs(a - b) <= 1e-12 }
function same(a: unknown, b: unknown): boolean { return JSON.stringify(a) === JSON.stringify(b) }
function pathValid(value: string, prefix = false): boolean { return Array.from(value).length <= 4096 && !/[\\:\u0000-\u001f\u007f-\u009f]/.test(value) && (!prefix || !/[*?\[\]]/.test(value)) && !value.split('/').some(x => !x || x === '.' || x === '..') }
function last<T>(values: T[]): T | undefined { return values[values.length - 1] }

export function validateDebugSearch(request: DebugRequest): string | null {
  const common = validateSearch(request)
  if (common) return common
  if (!['dense', 'bm25', 'hybrid'].includes(request.mode)) return '请选择有效的检索模式。'
  if (request.rerank !== false || request.query_expansion !== false) return 'Rerank 与 Query Expansion 尚未开放。'
  const f = request.filters
  if (f.language !== null && !/^[a-z][a-z0-9_+#-]{0,31}$/.test(f.language)) return 'Language 使用小写索引语言名。'
  if (f.path_prefix !== null && (f.path_prefix.trim() !== f.path_prefix || !pathValid(f.path_prefix.replace(/\/$/, ''), true))) return 'Path prefix 使用仓库相对路径和 /，不支持遍历或通配符。'
  return null
}

async function scopeFingerprint(request: DebugRequest): Promise<string> {
  const body = { filters: { language: request.filters.language, path_prefix: request.filters.path_prefix }, repository: request.repository, version: 'retrieval-scope-v1' }
  const hash = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(body)))
  return `sha256:${Array.from(new Uint8Array(hash), x => x.toString(16).padStart(2, '0')).join('')}`
}

function lineAt(content: string, position: number, first: number): number {
  const parts = content.split(/(\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029])/u)
  let offset = 0, line = first
  for (let i = 0; i < parts.length; i += 2) {
    const length = Array.from((parts[i] ?? '') + (parts[i + 1] ?? '')).length
    if (position >= offset && position < offset + length) return line
    offset += length; line++
  }
  throw invalid()
}

function parseCandidate(value: unknown, request: DebugRequest, base: DebugResponse, rev: string): DebugCandidate {
  const c = record(value), source = record(c.source_metadata)
  check(typeof c.chunk_id === 'string' && /^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(c.chunk_id))
  check(c.repository === request.repository && c.index_revision === rev && same(c.matched_queries, [request.query]))
  const scores: Record<string, number> = {}, ranks: Record<string, number> = {}
  for (const [name, score] of Object.entries(record(c.channel_scores))) { check(channels.includes(name as DebugChannel)); scores[name] = finite(score) }
  keys(record(c.channel_ranks), Object.keys(scores)); check(Object.keys(scores).length > 0)
  for (const [name, rank] of Object.entries(record(c.channel_ranks))) ranks[name] = integer(rank, 1, 60)
  const parsed = parseSearchResponse({ ...base, top_k: 1, results: [{ ...source, chunk_id: c.chunk_id, repository: c.repository, rank: 1, score: 0 }] }, { ...request, top_k: 1 }).results[0]!
  check(pathValid(parsed.path) && (!request.filters.language || parsed.language === request.filters.language)
    && (!request.filters.path_prefix || parsed.path.startsWith(request.filters.path_prefix)))
  const lines = parsed.content.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/u)
  if (last(lines) === '') lines.pop()
  check(lines.length === parsed.end_line - parsed.start_line + 1)
  const { rank: _rank, chunk_id: _id, repository: _repo, score: _score, ...metadata } = parsed
  return { chunk_id: c.chunk_id, repository: request.repository, index_revision: rev, source_metadata: metadata,
    channel_scores: scores, channel_ranks: ranks, matched_queries: [request.query] }
}

function parseBatch(value: unknown, name: DebugChannel, request: DebugRequest, base: DebugResponse, scope: string): DebugBatch {
  const raw = record(value)
  check(raw.repository === request.repository && raw.query === request.query && raw.channel === name && raw.index_revision === base.index_revision)
  check(same(raw.filters, request.filters) && raw.scope_fingerprint === scope)
  const limit = { dense: 30, bm25: 30, exact: 10, rrf: 60 }[name]
  check(raw.candidate_limit === limit && Array.isArray(raw.candidates) && raw.candidates.length <= limit)
  const candidates = raw.candidates.map(c => parseCandidate(c, request, base, base.index_revision))
  check(new Set(candidates.map(c => c.chunk_id)).size === candidates.length)
  candidates.forEach((c, i) => { check(c.channel_ranks[name] === i + 1); if (name !== 'rrf') keys(c.channel_scores, [name]) })
  const batch: DebugBatch = { repository: request.repository, query: request.query, channel: name, index_revision: base.index_revision,
    candidate_limit: limit, candidates, retrieval_latency_ms: finite(raw.retrieval_latency_ms, 0), filters: { ...request.filters }, scope_fingerprint: scope }
  if (name === 'exact') {
    const reasons = record(raw.reasons), match = raw.match === null ? null : record(raw.match)
    check(match === null || (typeof match.kind === 'string' && kinds.includes(match.kind) && typeof match.value === 'string'
      && match.value.trim() && Array.from(match.value).length <= 8192 && ['full', 'partial'].includes(String(match.mode))))
    check(match !== null || candidates.length === 0)
    keys(reasons, candidates.map(c => c.chunk_id))
    batch.match = match ? { kind: String(match.kind), value: String(match.value), mode: match.mode as 'full' | 'partial' } : null; batch.reasons = {}
    for (const c of candidates) {
      const r = record(reasons[c.chunk_id]), s = c.source_metadata
      check(r.kind === match!.kind && ['full', 'partial'].includes(String(r.strength)) && (r.strength !== 'partial' || match!.mode === 'partial'))
      check(typeof r.matched_value === 'string' && r.matched_value.length > 0 && typeof r.location === 'string')
      const start = integer(r.start_line, s.start_line, s.end_line), end = integer(r.end_line, start, s.end_line)
      const occurrences = integer(r.occurrences, 1, Array.from(s.content).length)
      if (r.location === 'metadata') {
        const fields: Record<string, string | null | undefined> = { symbol: s.symbol, short_symbol: s.symbol ? last(s.symbol.split('.')) : null, path: s.path, filename: last(s.path.split('/')) }
        const field = fields[String(match!.kind)]
        check(typeof field === 'string' && r.matched_value === field && start === s.start_line && end === s.end_line
          && r.char_start === null && r.char_end_exclusive === null && occurrences === 1)
        check(r.strength === 'full' ? field === match!.value : field.includes(String(match!.value)))
      } else {
        check(['lexical_literal', 'quoted_text', 'source_line', 'source_text'].includes(r.location))
        const a = integer(r.char_start, 0), b = integer(r.char_end_exclusive, a + 1, Array.from(s.content).length)
        check(Array.from(s.content).slice(a, b).join('') === r.matched_value && r.matched_value === match!.value)
        check(lineAt(s.content, a, s.start_line) === start && lineAt(s.content, b - 1, s.start_line) === end)
      }
      batch.reasons[c.chunk_id] = { kind: String(r.kind), strength: r.strength as MatchReason['strength'], location: r.location,
        matched_value: r.matched_value, start_line: start, end_line: end, char_start: r.char_start as number | null,
        char_end_exclusive: r.char_end_exclusive as number | null, occurrences }
      const full: Record<string, number> = { symbol: 100, short_symbol: 90, path: 80, filename: 70, text: 60, literal: 60 }
      const partial: Record<string, number> = { symbol: 40, short_symbol: 35, path: 30, filename: 25, text: 20, literal: 20 }
      check(c.channel_scores.exact === (r.strength === 'full' ? full : partial)[String(r.kind)])
    }
  }
  return batch
}

export async function parseDebugResponse(value: unknown, request: DebugRequest): Promise<DebugResponse> {
  check(validateDebugSearch(request) === null)
  const d = record(value), common = parseSearchResponse(value, request)
  check(d.mode === request.mode && same(d.filters, request.filters) && d.rerank === false && d.query_expansion === false)
  const rev = revision(d.index_revision), scoreType = { dense: 'cosine_similarity', bm25: 'bm25', hybrid: 'rrf' }[request.mode]
  check(d.score_type === scoreType)
  const limits = { dense: 30, bm25: 30, exact: 10, fusion: 60, rerank: 30, top_k: request.top_k }
  check(same(d.limits, limits))
  const base = { ...common, index_revision: rev } as DebugResponse
  const scope = await scopeFingerprint(request), names: DebugChannel[] = request.mode === 'hybrid' ? ['dense', 'bm25', 'exact'] : [request.mode]
  const rawChannels = record(d.channels); keys(rawChannels, names)
  const batches: DebugResponse['channels'] = {}
  for (const name of names) batches[name] = parseBatch(rawChannels[name], name, request, base, scope)
  const union = new Map<string, DebugCandidate>()
  for (const name of names) for (const candidate of batches[name]!.candidates) {
    const old = union.get(candidate.chunk_id)
    if (old) {
      check(same(old.source_metadata, candidate.source_metadata))
      old.channel_scores[name] = candidate.channel_scores[name]!; old.channel_ranks[name] = candidate.channel_ranks[name]!
    } else union.set(candidate.chunk_id, { ...candidate, channel_scores: { ...candidate.channel_scores }, channel_ranks: { ...candidate.channel_ranks } })
  }
  check(d.untruncated_union_count === union.size)
  let fused: DebugBatch | null = null, selected = batches[names[0]!]!, fusionMs = 0
  const contributions: DebugResponse['rrf_contributions'] = {}
  if (request.mode === 'hybrid') {
    check(d.rrf_k === 60)
    fused = parseBatch(d.fused, 'rrf', request, base, scope); selected = fused; fusionMs = fused.retrieval_latency_ms
    const scores = (c: DebugCandidate) => names.reduce((sum, ch) => sum + (c.channel_ranks[ch] ? 1 / (60 + c.channel_ranks[ch]) : 0), 0)
    const expected = [...union.values()].sort((a, b) => Math.abs(scores(a) - scores(b)) < 1e-15 ? a.chunk_id.localeCompare(b.chunk_id) : scores(b) - scores(a)).slice(0, 60)
    check(expected.length === fused.candidates.length)
    const rawContributions = record(d.rrf_contributions); keys(rawContributions, expected.map(c => c.chunk_id))
    fused.candidates.forEach((c, i) => {
      const original = expected[i]!
      check(c.chunk_id === original.chunk_id && same(c.source_metadata, original.source_metadata) && close(c.channel_scores.rrf!, scores(original)))
      keys(c.channel_scores, [...Object.keys(original.channel_scores), 'rrf']); keys(c.channel_ranks, [...Object.keys(original.channel_ranks), 'rrf'])
      const detail = record(rawContributions[c.chunk_id]); keys(detail, Object.keys(original.channel_ranks)); contributions[c.chunk_id] = {}
      for (const ch of Object.keys(original.channel_ranks)) {
        check(c.channel_scores[ch] === original.channel_scores[ch] && c.channel_ranks[ch] === original.channel_ranks[ch])
        const part = record(detail[ch]), rank = original.channel_ranks[ch]!, rawScore = original.channel_scores[ch]!
        check(part.rank === rank && part.raw_score === rawScore && close(finite(part.contribution, 0), 1 / (60 + rank)))
        contributions[c.chunk_id]![ch] = { rank, raw_score: rawScore, contribution: finite(part.contribution, 0) }
      }
    })
  } else { check(d.fused === null && d.rrf_k === null); keys(record(d.rrf_contributions), []) }
  check(common.results.length === Math.min(request.top_k, selected.candidates.length))
  common.results.forEach((r, i) => {
    const c = selected.candidates[i]!, { rank: _r, chunk_id: _c, repository: _p, score: _s, ...source } = r
    check(r.chunk_id === c.chunk_id && same(source, c.source_metadata) && r.score === c.channel_scores[request.mode === 'hybrid' ? 'rrf' : request.mode])
  })
  const timing = record(d.timings), rawTimes = record(timing.channel_ms), channelMs: Record<string, number> = {}; keys(rawTimes, names)
  for (const ch of names) { channelMs[ch] = finite(rawTimes[ch], 0); check(channelMs[ch] === batches[ch]!.retrieval_latency_ms) }
  check(finite(timing.fusion_ms, 0) === fusionMs && finite(timing.total_ms, 0) === common.retrieval_latency_ms)
  const overhead = finite(timing.overhead_ms, 0)
  check(Math.abs(common.retrieval_latency_ms - Object.values(channelMs).reduce((a, b) => a + b, 0) - fusionMs - overhead) <= .02)
  return { ...common, mode: request.mode, filters: { ...request.filters }, index_revision: rev, score_type: scoreType as DebugResponse['score_type'],
    limits, channels: batches, fused, rrf_k: request.mode === 'hybrid' ? 60 : null, untruncated_union_count: union.size,
    rrf_contributions: contributions, rerank: false, query_expansion: false,
    timings: { channel_ms: channelMs, fusion_ms: fusionMs, total_ms: common.retrieval_latency_ms, overhead_ms: overhead } }
}

export function parseSnapshotResponse(value: unknown, repository: string): SnapshotResponse {
  const d = record(value); check(d.repository === repository && typeof d.collection === 'string' && d.collection.length > 0)
  return { repository, collection: d.collection, index_revision: revision(d.index_revision), chunks: integer(d.chunks, 0, 10000), elapsed_ms: finite(d.elapsed_ms, 0) }
}
