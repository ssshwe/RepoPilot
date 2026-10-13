import type {
  ApiChatMessage,
  ChatResponse,
  ChatStreamDelta,
  ChatStreamMetadata,
  RequirementAnalysis,
} from '@/types/chat'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(
  /\/$/,
  '',
)

interface ChatStreamHandlers {
  onMetadata?: (metadata: ChatStreamMetadata) => void
  onDelta: (delta: ChatStreamDelta) => void
  onDone?: (response: ChatResponse) => void
}

interface StreamChatOptions {
  signal?: AbortSignal
}

interface ParsedEvent {
  event: string
  data: string
}

export class ChatStreamProtocolError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ChatStreamProtocolError'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function requiredString(
  record: Record<string, unknown>,
  key: string,
  event: string,
): string {
  const value = record[key]
  if (typeof value !== 'string') {
    throw new ChatStreamProtocolError(`流事件 ${event} 缺少字符串字段 ${key}。`)
  }
  return value
}

function requiredStringArray(
  record: Record<string, unknown>,
  key: string,
  context: string,
): string[] {
  const value = record[key]
  if (!Array.isArray(value) || !value.every((item) => typeof item === 'string')) {
    throw new ChatStreamProtocolError(`${context} 缺少字符串数组字段 ${key}。`)
  }
  return value
}

function parseJsonObject(data: string, event: string): Record<string, unknown> {
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch {
    throw new ChatStreamProtocolError(`流事件 ${event} 包含无效 JSON。`)
  }

  if (!isRecord(parsed)) {
    throw new ChatStreamProtocolError(`流事件 ${event} 的数据格式无效。`)
  }
  return parsed
}

function parseEvent(block: string): ParsedEvent | null {
  let event = 'message'
  const dataLines: string[] = []

  for (const rawLine of block.replace(/^\uFEFF/, '').split(/\r\n|\r|\n/)) {
    if (!rawLine || rawLine.startsWith(':')) {
      continue
    }

    const colonIndex = rawLine.indexOf(':')
    const field = colonIndex === -1 ? rawLine : rawLine.slice(0, colonIndex)
    let value = colonIndex === -1 ? '' : rawLine.slice(colonIndex + 1)
    if (value.startsWith(' ')) {
      value = value.slice(1)
    }

    if (field === 'event') {
      event = value
    } else if (field === 'data') {
      dataLines.push(value)
    }
  }

  if (dataLines.length === 0) {
    return null
  }
  return { event, data: dataLines.join('\n') }
}

function dispatchEvent(
  parsedEvent: ParsedEvent,
  handlers: ChatStreamHandlers,
): boolean {
  if (!['metadata', 'delta', 'done', 'error'].includes(parsedEvent.event)) {
    return false
  }
  const payload = parseJsonObject(parsedEvent.data, parsedEvent.event)

  switch (parsedEvent.event) {
    case 'metadata':
      handlers.onMetadata?.({
        model: requiredString(payload, 'model', parsedEvent.event),
      })
      return false
    case 'delta':
      handlers.onDelta({
        content: requiredString(payload, 'content', parsedEvent.event),
      })
      return false
    case 'done': {
      const rawMessage = payload.message
      if (!isRecord(rawMessage)) {
        throw new ChatStreamProtocolError('流事件 done 缺少完整消息。')
      }

      const role = requiredString(rawMessage, 'role', parsedEvent.event)
      if (role !== 'assistant') {
        throw new ChatStreamProtocolError('流事件 done 的消息角色必须是 assistant。')
      }

      handlers.onDone?.({
        message: {
          role,
          content: requiredString(rawMessage, 'content', parsedEvent.event),
        },
        model: requiredString(payload, 'model', parsedEvent.event),
      })
      return true
    }
    case 'error':
      throw new Error(requiredString(payload, 'detail', parsedEvent.event))
    default:
      return false
  }
}

async function readHttpError(response: Response, action = '请求'): Promise<string> {
  const fallback = `${action}失败（HTTP ${response.status}）。`
  try {
    const payload: unknown = await response.json()
    if (isRecord(payload) && typeof payload.detail === 'string') {
      return payload.detail
    }
  } catch {
    // The fallback remains useful when an upstream proxy returns HTML or no body.
  }
  return fallback
}

async function postJson(path: string, body: unknown, action: string): Promise<unknown> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  if (!response.ok) {
    throw new Error(await readHttpError(response, action))
  }

  try {
    return (await response.json()) as unknown
  } catch {
    throw new ChatStreamProtocolError(`${action}返回了无效 JSON。`)
  }
}

function parseChatResponse(payload: unknown, context: string): ChatResponse {
  if (!isRecord(payload) || !isRecord(payload.message)) {
    throw new ChatStreamProtocolError(`${context}缺少完整消息。`)
  }

  const role = requiredString(payload.message, 'role', context)
  if (role !== 'assistant') {
    throw new ChatStreamProtocolError(`${context}的消息角色必须是 assistant。`)
  }

  return {
    message: {
      role,
      content: requiredString(payload.message, 'content', context),
    },
    model: requiredString(payload, 'model', context),
  }
}

export async function chat(
  messages: readonly ApiChatMessage[],
): Promise<ChatResponse> {
  return parseChatResponse(
    await postJson('/api/chat', { messages }, '聊天请求'),
    '聊天响应',
  )
}

export async function runToolAgent(
  messages: readonly ApiChatMessage[],
): Promise<ChatResponse> {
  return parseChatResponse(
    await postJson('/api/agent', { messages }, '工具调用请求'),
    '工具调用响应',
  )
}

export async function analyzeRequirement(
  requirement: string,
): Promise<RequirementAnalysis> {
  const payload = await postJson('/api/analyze', { requirement }, '需求分析请求')
  if (!isRecord(payload)) {
    throw new ChatStreamProtocolError('需求分析响应格式无效。')
  }

  const difficulty = requiredString(payload, 'difficulty', '需求分析响应')
  if (!['easy', 'medium', 'hard'].includes(difficulty)) {
    throw new ChatStreamProtocolError('需求分析响应包含未知 difficulty。')
  }

  return {
    summary: requiredString(payload, 'summary', '需求分析响应'),
    difficulty: difficulty as RequirementAnalysis['difficulty'],
    tasks: requiredStringArray(payload, 'tasks', '需求分析响应'),
    risks: requiredStringArray(payload, 'risks', '需求分析响应'),
  }
}

export async function streamChat(
  messages: readonly ApiChatMessage[],
  handlers: ChatStreamHandlers,
  options: StreamChatOptions = {},
): Promise<void> {
  const response = await fetch(`${apiBaseUrl}/api/chat/stream`, {
    method: 'POST',
    headers: {
      Accept: 'text/event-stream',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ messages }),
    signal: options.signal,
  })

  if (!response.ok) {
    throw new Error(await readHttpError(response, '流式聊天请求'))
  }
  if (!response.headers.get('content-type')?.toLowerCase().includes('text/event-stream')) {
    throw new ChatStreamProtocolError('服务器没有返回 SSE 流。')
  }
  if (!response.body) {
    throw new ChatStreamProtocolError('浏览器没有收到可读取的响应流。')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let sawDone = false

  const processBlock = (block: string): void => {
    const event = parseEvent(block)
    if (!event) {
      return
    }
    if (sawDone) {
      throw new ChatStreamProtocolError('完整消息之后仍收到额外流事件。')
    }
    sawDone = dispatchEvent(event, handlers)
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })
      let separator = /\r\n\r\n|\n\n|\r\r/.exec(buffer)
      while (separator?.index !== undefined) {
        const block = buffer.slice(0, separator.index)
        buffer = buffer.slice(separator.index + separator[0].length)
        processBlock(block)
        if (sawDone) {
          try {
            await reader.cancel()
          } catch {
            // A complete message is authoritative even if cancellation fails.
          }
          return
        }
        separator = /\r\n\r\n|\n\n|\r\r/.exec(buffer)
      }
    }

    buffer += decoder.decode()
    if (buffer.trim()) {
      processBlock(buffer)
    }
    if (!sawDone) {
      throw new ChatStreamProtocolError('流在完整消息到达前结束。')
    }
  } catch (streamError) {
    try {
      await reader.cancel()
    } catch {
      // Preserve the original protocol, network, or server error.
    }
    throw streamError
  } finally {
    reader.releaseLock()
  }
}
