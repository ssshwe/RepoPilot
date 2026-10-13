import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

// Run the pure contract on the supported Node 20 runtime without adding a runner.
// Build separately checks its TypeScript types; these tests check API boundaries.
const source = await readFile(new URL('../src/api/retrieval-contract.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
})
const { validateSearch, parseSearchResponse } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
)

const request = { repository: 'demo-repository', query: '退款逻辑在哪里？', top_k: 5 }
function response() {
  return {
    ...request, collection: 'repopilot_code', embedding_model: 'qwen3-embedding:0.6b',
    embedding_dimension: 1024, retrieval_latency_ms: 25.2,
    results: [{
      rank: 1, chunk_id: '67881397-a785-5ed6-ad27-40ce2d67e8e6',
      repository: request.repository, score: 0.44413948,
      path: 'backend/service/RefundService.java', language: 'java',
      start_line: 1, end_line: 2, content: 'public void refund() {\n  wallets.credit(userId, amount);\n',
    }],
  }
}

test('accepts valid inputs and counts Unicode code points like the backend', () => {
  assert.equal(validateSearch(request), null)
  assert.equal(validateSearch({ ...request, query: '😀'.repeat(8192), top_k: 20 }), null)
  assert.match(validateSearch({ ...request, query: '😀'.repeat(8193) }), /8192/)
})

test('rejects empty queries, unsafe repository names and invalid TopK', () => {
  assert.ok(validateSearch({ ...request, query: ' \r\n\t' }))
  for (const repository of ['../demo', 'demo.', 'CON.java', 'NUL', '', 'a'.repeat(65)]) {
    assert.ok(validateSearch({ ...request, repository }), repository)
  }
  for (const top_k of [0, 21, 1.5, NaN, Infinity, true, '5']) {
    assert.ok(validateSearch({ ...request, top_k }))
  }
})

test('preserves source whitespace and raw score, projects only known fields', () => {
  const data = response()
  data.vector = [1, 2, 3]
  data.results[0].unexpected_payload = 'excluded'
  const parsed = parseSearchResponse(data, request)
  assert.equal(parsed.results[0].content, data.results[0].content)
  assert.equal(parsed.results[0].score, 0.44413948)
  assert.equal('vector' in parsed, false)
  assert.equal('unexpected_payload' in parsed.results[0], false)
})

test('accepts fewer than TopK results and an empty matching repository', () => {
  assert.equal(parseSearchResponse(response(), request).results.length, 1)
  const data = response()
  data.results = []
  assert.deepEqual(parseSearchResponse(data, request).results, [])
})

test('rejects stale request echoes and foreign repository payloads', () => {
  for (const change of [{ repository: 'foreign' }, { query: 'other' }, { top_k: 1 }]) {
    assert.throws(() => parseSearchResponse({ ...response(), ...change }, request))
  }
  const data = response()
  data.results[0].repository = 'foreign'
  assert.throws(() => parseSearchResponse(data, request), /仓库范围/)
})

test('rejects invalid ranking, duplicate chunk IDs and excess results', () => {
  const wrongRank = response()
  wrongRank.results[0].rank = 0
  assert.throws(() => parseSearchResponse(wrongRank, request))
  const duplicate = response()
  duplicate.results.push({ ...duplicate.results[0], rank: 2 })
  assert.throws(() => parseSearchResponse(duplicate, request))
  const excessive = response()
  excessive.results = Array.from({ length: 6 }, (_, i) => ({
    ...excessive.results[0], rank: i + 1, chunk_id: `chunk-${i}`,
  }))
  assert.throws(() => parseSearchResponse(excessive, request))
})

test('rejects malformed numeric metadata and source bounds', () => {
  for (const change of [
    { score: NaN }, { score: Infinity }, { score: '0.5' },
    { start_line: 0 }, { end_line: 0 }, { end_line: 1.5 },
    { path: '' }, { chunk_id: '' }, { content: '   ' }, { content: 'x'.repeat(8193) },
  ]) {
    const data = response()
    Object.assign(data.results[0], change)
    assert.throws(() => parseSearchResponse(data, request))
  }
  for (const change of [
    { embedding_dimension: 0 }, { embedding_dimension: 1.5 },
    { retrieval_latency_ms: -1 }, { retrieval_latency_ms: Infinity },
    { embedding_model: '' }, { collection: '' }, { results: null },
  ]) {
    assert.throws(() => parseSearchResponse({ ...response(), ...change }, request))
  }
  assert.throws(() => parseSearchResponse(null, request))
})


const ast = { symbol: 'RefundService.refund', node_type: 'method_declaration',
  parent_symbol: 'RefundService', chunking_strategy: 'ast', chunking_version: 'ast-v1' }

test('preserves complete AST metadata and handles unnamed module and fallback chunks', () => {
  for (const metadata of [ast, { ...ast, symbol: null, parent_symbol: null, node_type: 'program' },
    { symbol: null, parent_symbol: null, node_type: null,
      chunking_strategy: 'fallback', chunking_version: 'ast-fallback-line-v1' }]) {
    const data = response()
    Object.assign(data.results[0], metadata)
    const parsed = parseSearchResponse(data, request).results[0]
    for (const [key, value] of Object.entries(metadata)) assert.equal(parsed[key], value)
  }
  const legacy = parseSearchResponse(response(), request).results[0]
  assert.equal(legacy.chunking_strategy, 'simple')
  assert.equal(legacy.chunking_version, 'line-window-v1')
  assert.equal(legacy.symbol, null)
})

test('rejects incomplete or contradictory AST metadata without displaying partial results', () => {
  for (const key of Object.keys(ast)) {
    const data = response()
    Object.assign(data.results[0], ast)
    delete data.results[0][key]
    assert.throws(() => parseSearchResponse(data, request), /元数据/)
  }
  for (const change of [
    { symbol: '' }, { symbol: 1 }, { symbol: 'x\nkey' }, { symbol: 'x'.repeat(4097) },
    { symbol: null }, { node_type: null }, { node_type: '' }, { node_type: 1 },
    { parent_symbol: 'Other' }, { parent_symbol: true },
    { chunking_strategy: 'hybrid' }, { chunking_strategy: true },
    { chunking_strategy: 'simple' }, { chunking_strategy: 'fallback' },
    { chunking_version: 'line-window-v1' }, { chunking_version: null },
  ]) {
    const data = response()
    Object.assign(data.results[0], ast, change)
    assert.throws(() => parseSearchResponse(data, request), /元数据/)
  }
})
