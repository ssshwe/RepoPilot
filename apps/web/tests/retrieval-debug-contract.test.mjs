import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { moduleUrl } from './load-code-chat.mjs'

const { parseDebugResponse, validateDebugSearch, parseSnapshotResponse } = await import(await moduleUrl('api/retrieval-debug-contract'))
const fixture = JSON.parse(await readFile(new URL('./fixtures/retrieval-debug.json', import.meta.url), 'utf8'))
const copy = value => structuredClone(value)

for (const mode of ['dense', 'bm25', 'hybrid']) test(`${mode} preserves bounded final results, source coordinates and score category`, async () => {
  const parsed = await parseDebugResponse(copy(fixture.responses[mode]), fixture.requests[mode])
  assert.equal(parsed.mode, mode); assert.equal(parsed.results.length, 5)
  assert.equal(parsed.score_type, fixture.responses[mode].score_type)
  assert.deepEqual(parsed.results, fixture.responses[mode].results)
  assert.ok(Object.values(parsed.channels).every(b => b.candidates.length <= b.candidate_limit))
})

test('debug request validation rejects unsupported features and invalid structured scope', () => {
  assert.equal(validateDebugSearch(fixture.requests.hybrid), null)
  for (const change of [{ rerank: true }, { query_expansion: true }, { mode: 'exact' }, { filters: { language: 'JAVA', path_prefix: null } },
    { filters: { language: null, path_prefix: '../' } }, { filters: { language: null, path_prefix: 'a/*' } }]) {
    assert.ok(validateDebugSearch({ ...fixture.requests.hybrid, ...change }))
  }
})

for (const change of ['mode', 'revision', 'scope', 'filters', 'duplicate', 'foreign', 'coordinate', 'source_conflict', 'raw_rank', 'rrf_score', 'contribution', 'final_rank', 'final_score', 'timing', 'score_type', 'union']) {
  test(`rejects modified debug ${change}`, async () => {
    const value = copy(fixture.responses.hybrid)
    if (change === 'mode') value.mode = 'dense'
    else if (change === 'revision') value.channels.bm25.index_revision = `sha256:${'0'.repeat(64)}`
    else if (change === 'scope') value.channels.dense.scope_fingerprint = `sha256:${'0'.repeat(64)}`
    else if (change === 'filters') value.filters.language = 'rust'
    else if (change === 'duplicate') value.channels.dense.candidates[1] = copy(value.channels.dense.candidates[0])
    else if (change === 'foreign') value.channels.dense.candidates[0].repository = 'other'
    else if (change === 'coordinate') value.channels.dense.candidates[0].source_metadata.start_line += 1
    else if (change === 'source_conflict') value.channels.bm25.candidates[0].source_metadata.content = value.channels.bm25.candidates[0].source_metadata.content.replace('credit', 'broken')
    else if (change === 'raw_rank') value.fused.candidates[0].channel_ranks.dense = 60
    else if (change === 'rrf_score') value.fused.candidates[0].channel_scores.rrf += .1
    else if (change === 'contribution') Object.values(Object.values(value.rrf_contributions)[0])[0].contribution += .1
    else if (change === 'final_rank') value.results[0].rank = 2
    else if (change === 'final_score') value.results[0].score += .1
    else if (change === 'timing') value.timings.total_ms += 100
    else if (change === 'score_type') value.score_type = 'cosine_similarity'
    else value.untruncated_union_count += 1
    await assert.rejects(parseDebugResponse(value, fixture.requests.hybrid))
  })
}

test('Exact diagnostics are bound to real source coordinates and metadata', async () => {
  const value = copy(fixture.responses.hybrid)
  Object.values(value.channels.exact.reasons)[0].start_line += 1
  await assert.rejects(parseDebugResponse(value, fixture.requests.hybrid))
})

test('projects unknown response and Payload fields before rendering', async () => {
  const value = copy(fixture.responses.hybrid)
  value.vector = [1, 2]; value.channels.exact.match.unexpected = 'drop'
  value.channels.dense.candidates[0].source_metadata.private_payload = 'drop'
  const result = await parseDebugResponse(value, fixture.requests.hybrid)
  assert.equal('vector' in result, false)
  assert.equal('unexpected' in result.channels.exact.match, false)
  assert.equal('private_payload' in result.channels.dense.candidates[0].source_metadata, false)
})

test('snapshot metadata is request-bound and excludes server paths', () => {
  assert.deepEqual(parseSnapshotResponse({ ...fixture.snapshot, path: 'private' }, fixture.snapshot.repository), fixture.snapshot)
  assert.throws(() => parseSnapshotResponse(fixture.snapshot, 'other'))
})
