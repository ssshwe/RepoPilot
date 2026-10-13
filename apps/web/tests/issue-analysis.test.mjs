// Controlled contract/state fixtures. These tests do not invoke model inference.
import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl } from './load-code-chat.mjs'
import { response as codeResponse } from './code-chat-fixture.mjs'

const { validateIssue, parseIssueResponse, issueHttpError } = await import(await moduleUrl('api/issue-analysis-contract'))
const { useIssueAnalysisStore } = await import(await moduleUrl('stores/issue-analysis'))
const request = { repository: 'demo-repository', issue: '重复退款检查', top_k: 5 }
function fixture(req = request) {
  const data = codeResponse()
  return { repository: req.repository, issue: req.issue, summary: '检查重复处理',
    related_code: data.citations.map(c => ({ ...c, repository: req.repository })), possible_call_chain: [],
    analysis: ['当前片段无法确认缺陷'], modification_suggestions: ['评估幂等保护'], test_suggestions: ['重复请求同一订单'],
    risks: ['需要检查索引外代码'], source_ids: data.source_ids, sources: data.sources, status: 'analyzed', model: data.model,
    retrieval: { ...data.retrieval, top_k: req.top_k }, generation: data.generation }
}
const reply = data => ({ ok: true, status: 200, json: async () => data })
function store() {
  setActivePinia(createPinia())
  const s = useIssueAnalysisStore()
  s.repository = request.repository; s.issue = request.issue
  return s
}
function deferred() {
  let resolve
  return { promise: new Promise(r => { resolve = r }), resolve: value => resolve(value) }
}

test('Issue input limits and JSON framing budget match backend', () => {
  assert.equal(validateIssue(request), null)
  for (const issue of ['', ' ', '😀'.repeat(8193), '\ud800', '\x00'.repeat(8192)]) assert.ok(validateIssue({ ...request, issue }))
  assert.equal(validateIssue({ ...request, issue: '😀'.repeat(8192) }), null)
  assert.ok(validateIssue({ ...request, repository: '../demo' }))
  assert.ok(validateIssue({ ...request, top_k: 21 }))
})
test('Issue response preserves validated metadata and preview text', () => {
  const data = fixture()
  data.possible_call_chain = [data.related_code[0]]
  assert.deepEqual(parseIssueResponse(data, request), data)
})
test('Issue contract rejects foreign/stale responses and corrupted previews', () => {
  for (const mutate of [d => { d.repository = 'foreign' }, d => { d.issue = 'stale' },
    d => { d.retrieval.top_k = 4 }, d => { d.sources = [] }, d => { d.sources[0].content = 'one line' },
    d => { d.related_code[0].path = '../secret' }, d => { d.source_ids = ['S99'] }]) {
    const data = fixture(); mutate(data)
    assert.throws(() => parseIssueResponse(data, request), /未展示/)
  }
})
test('Candidate call chain must reference complete matching Citation metadata', () => {
  for (const mutate of [d => { d.possible_call_chain = [{ ...d.related_code[0], source_id: 'S99' }] },
    d => { d.possible_call_chain = [{ ...d.related_code[0], path: 'made-up.py' }] },
    d => { d.possible_call_chain = [d.related_code[0], d.related_code[0]] },
    d => { d.possible_call_chain = ['S1'] }]) {
    const data = fixture(); mutate(data)
    assert.throws(() => parseIssueResponse(data, request), /未展示/)
  }
})
test('Empty evidence has no generated claims or sources', () => {
  const data = fixture()
  Object.assign(data, { summary: '当前证据不足', related_code: [], possible_call_chain: [], source_ids: [], sources: [],
    status: 'insufficient_evidence', model: null, analysis: [], modification_suggestions: [], test_suggestions: [], risks: [],
    generation: { called: false, latency_ms: 0 } })
  Object.assign(data.retrieval, { retrieved_chunks: 0, used_sources: 0, context_characters: 0, empty_reason: 'no_results' })
  assert.deepEqual(parseIssueResponse(data, request), data)
  data.analysis = ['unsupported claim']
  assert.throws(() => parseIssueResponse(data, request), /未展示/)
})
test('Generated text arrays are bounded, UTF8 and nonblank, without active markup', () => {
  for (const value of ['not array', [' '], ['\ud800'], ['a'.repeat(1025)], ['a','a','a','a','a','a']]) {
    const data = fixture(); data.risks = value
    assert.throws(() => parseIssueResponse(data, request), /未展示/)
  }
  const data = fixture(); data.analysis = ['<img onerror=alert(1)> [unsafe](javascript:alert(1))']
  assert.equal(parseIssueResponse(data, request).analysis[0], data.analysis[0])
})
test('Issue errors never reflect raw upstream details', () => {
  assert.match(issueHttpError(502, 'Issue analysis generation returned potentially sensitive output.'), /敏感信息/)
  for (const status of [404, 409, 422, 502, 503, 500]) assert.ok(!issueHttpError(status, 'PRIVATE_SECRET').includes('PRIVATE'))
})
test('Issue response projects known fields without exposing raw debug data', () => {
  const data = fixture(); data.debug = 'PRIVATE'; data.possible_call_chain = [{ ...data.related_code[0], secret: 'PRIVATE' }]
  assert.ok(!JSON.stringify(parseIssueResponse(data, request)).includes('PRIVATE'))
})
test('Invalid Issue creates no fetch or submitted state', async () => {
  const previous = globalThis.fetch
  try {
    globalThis.fetch = () => { throw new Error('must not fetch') }
    const s = store(); s.issue = ' '
    await s.analyze()
    assert.equal(s.submitted, null); assert.ok(s.validationError); assert.equal(s.isBusy, false)
  } finally { globalThis.fetch = previous }
})
test('Single Issue submission snapshots params and disables duplicate generation', async () => {
  const previous = globalThis.fetch, pending = deferred(); const calls = []
  try {
    globalThis.fetch = (url, options) => { calls.push({url, options}); return pending.promise }
    const s = store(), operation = s.analyze()
    await s.analyze(); assert.equal(calls.length, 1); assert.equal(s.isBusy, true)
    assert.ok(calls[0].url.endsWith('/api/issue-analysis'))
    const posted = JSON.parse(calls[0].options.body)
    assert.deepEqual(posted, request)
    s.issue = 'edited later'
    pending.resolve(reply(fixture(posted))); await operation
    assert.equal(s.response.issue, request.issue); assert.equal(s.submitted.issue, request.issue)
    assert.equal(s.isBusy, false)
  } finally { globalThis.fetch = previous }
})
test('Clear/cancel ignores late Issue response and preserves newer busy state', async () => {
  const previous = globalThis.fetch, old = deferred(), next = deferred(); let n = 0
  try {
    globalThis.fetch = () => (++n === 1 ? old.promise : next.promise)
    const s = store(), first = s.analyze()
    s.clear(); assert.equal(s.submitted, null); assert.equal(s.isBusy, false)
    s.issue = 'new Issue'; const second = s.analyze()
    old.resolve(reply(fixture())); await first
    assert.equal(s.response, null); assert.equal(s.isBusy, true)
    next.resolve(reply(fixture({ ...request, issue: 'new Issue' }))); await second
    assert.equal(s.response.issue, 'new Issue'); assert.equal(s.isBusy, false)
  } finally { globalThis.fetch = previous }
})
test('Sensitive 502 has no result; retry uses originally submitted Issue', async () => {
  const previous = globalThis.fetch; const posted = []
  try {
    globalThis.fetch = async (_url, options) => {
      posted.push(JSON.parse(options.body))
      return posted.length === 1 ? { ok: false, status: 502, json: async () => ({ detail: 'Issue analysis generation returned potentially sensitive output.' }) }
        : reply(fixture(posted[1]))
    }
    const s = store(); await s.analyze()
    assert.equal(s.response, null); assert.match(s.error, /敏感信息/)
    s.issue = 'edited'; await s.retry()
    assert.deepEqual(posted[0], posted[1]); assert.equal(s.response.issue, request.issue)
  } finally { globalThis.fetch = previous }
})
test('Malformed and network Issue failures leave controlled errors without analysis', async () => {
  const previous = globalThis.fetch
  try {
    for (const fetcher of [async () => reply({ unexpected: 'PRIVATE' }), async () => { throw new TypeError('PRIVATE_NETWORK') }]) {
      globalThis.fetch = fetcher
      const s = store(); await s.analyze()
      assert.equal(s.response, null); assert.equal(s.isBusy, false); assert.ok(s.error); assert.ok(!s.error.includes('PRIVATE'))
    }
  } finally { globalThis.fetch = previous }
})
