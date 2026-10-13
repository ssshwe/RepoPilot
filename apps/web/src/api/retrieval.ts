import { parseSearchResponse } from '@/api/retrieval-contract'
import type { RetrievalSearchRequest, RetrievalSearchResponse } from '@/types/retrieval'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')

function httpError(status: number): string {
  switch (status) {
    case 404: return '索引集合不存在。请先为仓库建立索引；若接口不存在，请重启后端。'
    case 409: return 'Embedding 模型或索引集合不兼容，请检查索引时使用的模型配置。'
    case 422: return '搜索参数无效，请检查 Repository、Query 和 TopK。'
    case 502: return 'Embedding 或检索结果校验失败，请检查模型服务和索引后重试。'
    case 503: return '检索服务不可用，请检查 Ollama、Qdrant 和后端配置后重试。'
    default: return `搜索失败（HTTP ${status}），请稍后重试。`
  }
}

export async function searchCode(
  request: RetrievalSearchRequest, signal: AbortSignal,
): Promise<RetrievalSearchResponse> {
  const response = await fetch(`${apiBaseUrl}/api/retrieval/search`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request), signal,
  })
  if (!response.ok) throw new Error(httpError(response.status))
  let data: unknown
  try {
    data = await response.json()
  } catch {
    throw new Error('后端返回了无效的检索数据，请检查 API 地址后重试。')
  }
  return parseSearchResponse(data, request)
}
