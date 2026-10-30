import { parseDebugResponse, parseSnapshotResponse } from '@/api/retrieval-debug-contract'
import type { DebugRequest, DebugResponse, SnapshotResponse } from '@/types/retrieval-debug'

const base = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')
async function post(path: string, body: unknown, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(`${base}/api/retrieval/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
  if (!response.ok) {
    const errors: Record<number, string> = {
      404: '索引集合不存在，请先建立仓库索引。',
      409: '检索快照未准备、正在同步或索引已变化。请在索引稳定后点击“同步检索快照”。',
      422: '检索参数无效，请检查仓库、Query、TopK 和过滤条件。',
      502: '检索或来源校验失败，请检查 Embedding 服务及索引后重试。',
      503: '检索服务不可用，请检查 Qdrant、模型服务和本地快照存储。',
    }
    throw new Error(errors[response.status] ?? `检索失败（HTTP ${response.status}），请稍后重试。`)
  }
  try { return await response.json() } catch { throw new Error('后端返回了无效的检索诊断数据。') }
}
export async function debugSearch(request: DebugRequest, signal: AbortSignal): Promise<DebugResponse> {
  return parseDebugResponse(await post('debug', request, signal), request)
}
export async function syncSnapshot(repository: string, signal: AbortSignal): Promise<SnapshotResponse> {
  return parseSnapshotResponse(await post('debug/snapshot', { repository }, signal), repository)
}
