import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { askCode } from '@/api/code-chat'
import { validateCodeChat } from '@/api/code-chat-contract'
import type { CodeChatRequest, CodeChatTurn } from '@/types/code-chat'

export const useCodeChatStore = defineStore('code-chat', () => {
  const repository = ref('demo-repository')
  const repositories = ref(['demo-repository'])
  const question = ref('')
  const topK = ref<number | string>(5)
  const turns = ref<CodeChatTurn[]>([])
  const isBusy = ref(false)
  const validationError = ref<string | null>(null)
  const model = computed(() => [...turns.value].reverse().find(t => t.response)?.response?.model ?? null)
  let controller: AbortController | null = null
  let nextId = 0

  async function ask(): Promise<void> {
    if (isBusy.value) return
    const request: CodeChatRequest = { repository: repository.value.trim(), question: question.value,
      top_k: typeof topK.value === 'number' ? topK.value : Number.NaN }
    validationError.value = validateCodeChat(request)
    if (validationError.value) return
    repository.value = request.repository
    if (!repositories.value.includes(request.repository)) repositories.value.push(request.repository)
    const id = ++nextId
    turns.value.push({ id, request: { ...request }, state: 'pending', response: null, error: null })
    if (turns.value.length > 20) turns.value.shift()
    const pending = new AbortController()
    controller = pending
    isBusy.value = true
    let timedOut = false
    const timeout = globalThis.setTimeout(() => { timedOut = true; pending.abort() }, 180000)
    try {
      const response = await askCode(request, pending.signal)
      if (controller !== pending || pending.signal.aborted) return
      const turn = turns.value.find(t => t.id === id)
      if (turn) { turn.response = response; turn.state = 'complete' }
    } catch (failure) {
      if (controller !== pending) return
      const turn = turns.value.find(t => t.id === id)
      if (turn) {
        turn.state = pending.signal.aborted ? 'cancelled' : 'error'
        turn.error = pending.signal.aborted
          ? (timedOut ? '等待回答超时，请检查服务后重试。' : '已停止等待，本次未展示回答。')
          : failure instanceof TypeError ? '无法连接问答 API，请确认后端已启动且地址配置正确。'
            : failure instanceof Error ? failure.message : '问答失败，请稍后重试。'
      }
    } finally {
      globalThis.clearTimeout(timeout)
      if (controller === pending) { controller = null; isBusy.value = false }
    }
  }

  function cancel(): void {
    if (!controller) return
    controller.abort()
    controller = null
    isBusy.value = false
    const turn = turns.value.find(t => t.state === 'pending')
    if (turn) { turn.state = 'cancelled'; turn.error = '已停止等待，本次未展示回答。' }
  }
  function clear(): void { cancel(); turns.value = []; validationError.value = null }
  async function retry(turn: CodeChatTurn): Promise<void> {
    if (isBusy.value) return
    repository.value = turn.request.repository
    question.value = turn.request.question
    topK.value = turn.request.top_k
    await ask()
  }
  return { repository, repositories, question, topK, turns, isBusy, validationError, model, ask, cancel, clear, retry }
})
