import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  analyzeRequirement,
  chat,
  runToolAgent,
  streamChat,
} from '@/api/chat'
import type {
  ApiChatMessage,
  ConversationMessage,
  RequirementAnalysis,
  WorkspaceMode,
} from '@/types/chat'

let nextMessageId = 0

function createMessageId(): string {
  nextMessageId += 1
  return `message-${Date.now()}-${nextMessageId}`
}

function errorMessage(error: unknown, mode: WorkspaceMode): string {
  if (error instanceof Error && error.message) {
    return error.message
  }
  if (mode === 'analyze') {
    return '需求分析失败，请稍后重试。'
  }
  return '请求意外中断，请稍后重试。'
}

export const useChatStore = defineStore('chat', () => {
  const activeMode = ref<WorkspaceMode>('stream')
  const systemPrompt = ref('你是 RepoPilot，一名简洁、可靠的软件工程助手。')
  const messages = ref<ConversationMessage[]>([])
  const model = ref<string | null>(null)
  const analysis = ref<RequirementAnalysis | null>(null)
  const analyzedRequirement = ref<string | null>(null)
  const isBusy = ref(false)
  const error = ref<string | null>(null)

  function buildRequestMessages(): ApiChatMessage[] {
    const requestMessages: ApiChatMessage[] = []
    if (systemPrompt.value.trim()) {
      requestMessages.push({
        role: 'system',
        content: systemPrompt.value,
      })
    }

    requestMessages.push(
      ...messages.value
        .filter((message) => message.status === 'complete')
        .map(({ role, content }) => ({ role, content })),
    )
    return requestMessages
  }

  async function sendAnalysis(requirement: string): Promise<void> {
    analyzedRequirement.value = requirement
    analysis.value = null
    try {
      analysis.value = await analyzeRequirement(requirement)
    } catch (requestError) {
      error.value = errorMessage(requestError, 'analyze')
    }
  }

  async function sendConversation(
    content: string,
    mode: Exclude<WorkspaceMode, 'analyze'>,
  ): Promise<void> {
    messages.value.push({
      id: createMessageId(),
      role: 'user',
      content,
      status: 'complete',
    })
    const requestMessages = buildRequestMessages()
    const assistantMessage: ConversationMessage = {
      id: createMessageId(),
      role: 'assistant',
      content: '',
      status: mode === 'stream' ? 'streaming' : 'pending',
    }
    messages.value.push(assistantMessage)

    try {
      if (mode === 'stream') {
        await streamChat(requestMessages, {
          onMetadata: (metadata) => {
            model.value = metadata.model
          },
          onDelta: (delta) => {
            assistantMessage.content += delta.content
          },
          onDone: (response) => {
            model.value = response.model
            assistantMessage.content = response.message.content
            assistantMessage.status = 'complete'
          },
        })
        return
      }

      const response =
        mode === 'agent'
          ? await runToolAgent(requestMessages)
          : await chat(requestMessages)
      model.value = response.model
      assistantMessage.content = response.message.content
      assistantMessage.status = 'complete'
    } catch (requestError) {
      assistantMessage.status = 'failed'
      error.value = errorMessage(requestError, mode)
    }
  }

  async function send(content: string): Promise<void> {
    if (isBusy.value) {
      return
    }
    if (!content.trim()) {
      error.value = '请输入非空内容。'
      return
    }

    const mode = activeMode.value
    error.value = null
    isBusy.value = true
    try {
      if (mode === 'analyze') {
        await sendAnalysis(content)
      } else {
        await sendConversation(content, mode)
      }
    } finally {
      isBusy.value = false
    }
  }

  function clearCurrent(): void {
    error.value = null
    if (activeMode.value === 'analyze') {
      analysis.value = null
      analyzedRequirement.value = null
      return
    }
    messages.value = []
  }

  function dismissError(): void {
    error.value = null
  }

  return {
    activeMode,
    systemPrompt,
    messages,
    model,
    analysis,
    analyzedRequirement,
    isBusy,
    error,
    send,
    clearCurrent,
    dismissError,
  }
})
