import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl } from './load-code-chat.mjs'

const { useRetrievalDebugStore } = await import(await moduleUrl('stores/retrieval-debug'))
const fixture = JSON.parse(await readFile(new URL('./fixtures/retrieval-debug.json', import.meta.url), 'utf8'))
function store() {
  setActivePinia(createPinia()); const s = useRetrievalDebugStore()
  Object.assign(s, { repository: fixture.requests.hybrid.repository, query: fixture.requests.hybrid.query, mode: 'hybrid' })
  return s
}
function pendingFetch(t) {
  const calls = []
  t.mock.method(globalThis, 'fetch', (url, options) => new Promise((resolve, reject) => calls.push({ url, options, resolve, reject })))
  return calls
}
function reply(call) {
  const body = JSON.parse(call.options.body)
  call.resolve({ ok: true, status: 200, json: async () => structuredClone(call.url.endsWith('/snapshot') ? fixture.snapshot : fixture.responses[body.mode]) })
}

test('debug cancellation releases controls and ignores a late successful response', async t => {
  const calls = pendingFetch(t), s = store(), pending = s.search()
  s.cancel(); assert.equal(s.isBusy, false); assert.equal(calls[0].options.signal.aborted, true)
  reply(calls[0]); await pending
  assert.equal(s.response, null); assert.match(s.error, /取消/)
})

test('new mode result survives an older cancelled Hybrid request', async t => {
  const calls = pendingFetch(t), s = store(), first = s.search()
  s.cancel(); s.clear(); s.mode = 'bm25'; const second = s.search()
  reply(calls[1]); await second; assert.equal(s.response.mode, 'bm25')
  reply(calls[0]); await first
  assert.equal(s.response.mode, 'bm25'); assert.equal(s.error, null); assert.equal(s.isBusy, false)
})

test('late old rejection does not clear a newer busy state', async t => {
  const calls = pendingFetch(t), s = store(), first = s.search()
  s.cancel(); s.mode = 'dense'; const second = s.search()
  calls[0].reject(new TypeError('late')); await first
  assert.equal(s.error, null); assert.equal(s.isBusy, true)
  reply(calls[1]); await second; assert.equal(s.response.mode, 'dense')
})

test('snapshot cancellation cannot publish old success over a newer search', async t => {
  const calls = pendingFetch(t), s = store(), first = s.synchronize()
  assert.equal(s.operation, 'snapshot'); s.cancel(); s.mode = 'bm25'; const second = s.search()
  reply(calls[0]); await first; assert.equal(s.snapshot, null); assert.equal(s.isBusy, true)
  reply(calls[1]); await second; assert.equal(s.response.mode, 'bm25')
})

test('debug timeout ignores transport success after abort', async t => {
  const timers = []
  t.mock.method(globalThis, 'setTimeout', fn => { timers.push(fn); return 1 }); t.mock.method(globalThis, 'clearTimeout', () => {})
  const calls = pendingFetch(t), s = store(), pending = s.search()
  timers[0](); assert.equal(s.isBusy, false); assert.match(s.error, /超时/)
  reply(calls[0]); await pending; assert.equal(s.response, null); assert.match(s.error, /超时/)
})

test('changed mode or filter marks displayed results as previous parameters', async t => {
  const calls = pendingFetch(t), s = store(), pending = s.search()
  reply(calls[0]); await pending; assert.equal(s.parametersChanged, false)
  s.pathPrefix = 'backend/wallet/'; assert.equal(s.parametersChanged, true)
  s.pathPrefix = ''; s.mode = 'bm25'; assert.equal(s.parametersChanged, true)
})

test('invalid filter never fetches; active network failure remains visible', async t => {
  const calls = pendingFetch(t), s = store(); s.pathPrefix = '../'; await s.search()
  assert.equal(calls.length, 0); assert.match(s.error, /prefix/)
  s.pathPrefix = ''; const pending = s.search(); calls[0].reject(new TypeError('offline')); await pending
  assert.equal(s.isBusy, false); assert.match(s.error, /无法连接/)
})
