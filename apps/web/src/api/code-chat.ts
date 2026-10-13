import { codeChatHttpError, parseCodeChatResponse } from '@/api/code-chat-contract'
import type { CodeChatRequest, CodeChatResponse } from '@/types/code-chat'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')
export async function askCode(request: CodeChatRequest, signal: AbortSignal): Promise<CodeChatResponse> {
  const response = await fetch(`${apiBaseUrl}/api/code-chat`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal,
  })
  if (!response.ok) {
    let detail: unknown
    try { detail = (await response.json())?.detail } catch { /* Never reflect raw error bodies. */ }
    throw new Error(codeChatHttpError(response.status, detail))
  }
  let data: unknown
  try { data = await response.json() } catch { throw new Error('后端返回了无效的问答数据，请检查 API 地址后重试。') }
  return parseCodeChatResponse(data, request)
}
