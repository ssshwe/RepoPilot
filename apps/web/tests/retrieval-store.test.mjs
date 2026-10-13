import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl } from './load-code-chat.mjs'

const { useRetrievalStore } = await import(await moduleUrl('stores/retrieval'))
function store() { setActivePinia(createPinia()); const s = useRetrievalStore(); s.query = 'refund'; return s }
function pendingFetch(t) {
  const calls = []
  t.mock.method(globalThis, 'fetch', (url, options) => new Promise((resolve, reject) => calls.push({ url, options, resolve, reject })))
  return calls
}
function reply(call) {
  const request = JSON.parse(call.options.body)
  call.resolve({ ok: true, status: 200, json: async () => ({ ...request, collection: 'test',
    embedding_model: 'test-embedding', embedding_dimension: 1024, retrieval_latency_ms: 1, results: [] }) })
}

test('cancelled retrieval ignores successful late response and releases busy state', async t => {
  const calls = pendingFetch(t), s = store(), pending = s.search()
  s.cancel()
  assert.equal(calls[0].options.signal.aborted, true)
  assert.equal(s.isBusy, false)
  reply(calls[0]); await pending
  assert.equal(s.response, null); assert.match(s.error, /取消/)
})

test('cancel clear and newer search survive old successful retrieval', async t => {
  const calls = pendingFetch(t), s = store(), first = s.search()
  s.cancel(); s.clear(); s.query = 'new query'; const second = s.search()
  reply(calls[0]); await first
  assert.equal(s.response, null); assert.equal(s.error, null); assert.equal(s.isBusy, true)
  reply(calls[1]); await second
  assert.equal(s.response.query, 'new query'); assert.equal(s.isBusy, false)
})

test('old retrieval rejection cannot replace a newer search error or busy state', async t => {
  const calls = pendingFetch(t), s = store(), first = s.search()
  s.cancel(); s.query = 'new query'; const second = s.search()
  calls[0].reject(new TypeError('late network error')); await first
  assert.equal(s.error, null); assert.equal(s.isBusy, true)
  reply(calls[1]); await second
  assert.equal(s.response.query, 'new query'); assert.equal(s.error, null)
})

test('retrieval timeout releases controls and ignores a transport that continues after abort', async t => {
  const timers = []
  t.mock.method(globalThis, 'setTimeout', callback => { timers.push(callback); return 1 })
  t.mock.method(globalThis, 'clearTimeout', () => {})
  const calls = pendingFetch(t), s = store(), pending = s.search()
  timers[0]()
  assert.equal(s.isBusy, false); assert.match(s.error, /超时/)
  reply(calls[0]); await pending
  assert.equal(s.response, null); assert.match(s.error, /超时/)
})

test('active retrieval network failure remains visible and releases controls', async t => {
  const calls = pendingFetch(t), s = store(), pending = s.search()
  calls[0].reject(new TypeError('offline')); await pending
  assert.equal(s.response, null); assert.equal(s.isBusy, false); assert.match(s.error, /无法连接/)
})
