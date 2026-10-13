import { issueHttpError, parseIssueResponse } from '@/api/issue-analysis-contract'
import type { IssueAnalysisRequest, IssueAnalysisResponse } from '@/types/issue-analysis'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')
export async function analyzeIssue(request: IssueAnalysisRequest, signal: AbortSignal): Promise<IssueAnalysisResponse> {
  const response = await fetch(`${apiBaseUrl}/api/issue-analysis`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal,
  })
  if (!response.ok) {
    let detail: unknown
    try { detail = (await response.json())?.detail } catch { /* Never reflect upstream bodies. */ }
    throw new Error(issueHttpError(response.status, detail))
  }
  let data: unknown
  try { data = await response.json() } catch { throw new Error('后端返回了无效的 Issue 分析数据。') }
  return parseIssueResponse(data, request)
}
