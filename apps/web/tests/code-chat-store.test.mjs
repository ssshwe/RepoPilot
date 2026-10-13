import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { moduleUrl } from './load-code-chat.mjs'
import { request, response } from './code-chat-fixture.mjs'

const { useCodeChatStore } = await import(await moduleUrl('stores/code-chat'))
function store() { setActivePinia(createPinia()); const s = useCodeChatStore(); s.question = request.question; return s }
function pendingFetch(t) {
  const calls = []
  t.mock.method(globalThis, 'fetch', (url, options) => new Promise(resolve => calls.push({ url, options, resolve })))
  return calls
}
function reply(call, data = response(JSON.parse(call.options.body)), status = 200) {
  call.resolve({ ok: status === 200, status, json: async () => data })
}

test('invalid input creates no request or history turn', async t => {
  const calls = pendingFetch(t), s = store(); s.question = ' '
  await s.ask(); assert.equal(calls.length, 0); assert.equal(s.turns.length, 0); assert.ok(s.validationError)
})
test('one request per question and history keeps original parameters', async t => {
  const calls = pendingFetch(t), s = store(), first = s.ask()
  await s.ask(); assert.equal(calls.length, 1)
  s.repository = 'other'; s.question = 'next'
  reply(calls[0]); await first
  assert.equal(s.turns[0].state, 'complete')
  assert.equal(s.turns[0].request.repository, request.repository)
  assert.equal(s.turns[0].response.question, request.question)
  assert.equal(s.isBusy, false)
})
test('cancelled late response cannot replace a newer question or clear its busy state', async t => {
  const calls = pendingFetch(t), s = store(), first = s.ask()
  s.cancel(); assert.equal(calls[0].options.signal.aborted, true)
  s.question = 'new question'; const second = s.ask()
  reply(calls[0]); await first
  assert.equal(s.isBusy, true); assert.equal(s.turns[0].state, 'cancelled'); assert.equal(s.turns[0].response, null)
  reply(calls[1]); await second
  assert.equal(s.turns[1].state, 'complete'); assert.equal(s.turns[1].response.question, 'new question')
})
test('clearing a pending turn prevents it from reappearing later', async t => {
  const calls = pendingFetch(t), s = store(), promise = s.ask()
  s.clear(); reply(calls[0]); await promise
  assert.equal(s.turns.length, 0); assert.equal(s.isBusy, false)
})
test('controlled privacy error retains question and never displays partial answer', async t => {
  const calls = pendingFetch(t), s = store(), promise = s.ask()
  reply(calls[0], { detail: 'Code chat generation returned potentially sensitive output.' }, 502); await promise
  assert.equal(s.turns[0].state, 'error'); assert.equal(s.turns[0].response, null)
  assert.match(s.turns[0].error, /敏感信息/)
})
test('retry uses failed turn repository and question after form edits', async t => {
  const calls = pendingFetch(t), s = store(), first = s.ask()
  reply(calls[0], {}, 503); await first
  const failed = s.turns[0]; s.repository = 'other'; s.question = 'other'
  const second = s.retry(failed)
  assert.deepEqual(JSON.parse(calls[1].options.body), request)
  reply(calls[1]); await second; assert.equal(s.turns[1].state, 'complete')
})
test('malformed successful response and network failure do not become answers', async t => {
  const calls = pendingFetch(t), s = store(), first = s.ask()
  reply(calls[0], { ...response(), repository: 'foreign' }); await first
  assert.equal(s.turns[0].response, null); assert.equal(s.turns[0].state, 'error')
  t.mock.method(globalThis, 'fetch', async () => { throw new TypeError('network') })
  await s.ask(); assert.match(s.turns[1].error, /无法连接/); assert.equal(s.turns[1].response, null)
})
