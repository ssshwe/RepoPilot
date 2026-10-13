import { defineStore } from 'pinia'
import { ref } from 'vue'

import { analyzeIssue } from '@/api/issue-analysis'
import { validateIssue } from '@/api/issue-analysis-contract'
import type { IssueAnalysisRequest, IssueAnalysisResponse } from '@/types/issue-analysis'

export const useIssueAnalysisStore = defineStore('issue-analysis', () => {
  const repository = ref('demo-repository'), issue = ref(''), topK = ref<number | string>(5)
  const repositories = ref(['demo-repository'])
  const response = ref<IssueAnalysisResponse | null>(null)
  const submitted = ref<IssueAnalysisRequest | null>(null)
  const error = ref<string | null>(null), validationError = ref<string | null>(null), isBusy = ref(false)
  let controller: AbortController | null = null
  async function analyze(): Promise<void> {
    if (isBusy.value) return
    const request = { repository: repository.value.trim(), issue: issue.value,
      top_k: typeof topK.value === 'number' ? topK.value : Number.NaN }
    validationError.value = validateIssue(request)
    if (validationError.value) return
    repository.value = request.repository
    if (!repositories.value.includes(request.repository)) repositories.value.push(request.repository)
    submitted.value = { ...request }; response.value = null; error.value = null
    const pending = new AbortController()
    controller = pending; isBusy.value = true
    let timedOut = false
    const timeout = globalThis.setTimeout(() => { timedOut = true; pending.abort() }, 180000)
    try {
      const result = await analyzeIssue(request, pending.signal)
      if (controller === pending && !pending.signal.aborted) response.value = result
    } catch (failure) {
      if (controller !== pending) return
      error.value = pending.signal.aborted ? (timedOut ? '等待分析超时，请检查服务后重试。' : '已停止等待，本次未展示分析。')
        : failure instanceof TypeError ? '无法连接 Issue 分析 API，请确认后端已启动。'
          : failure instanceof Error ? failure.message : 'Issue 分析失败，请重试。'
    } finally {
      globalThis.clearTimeout(timeout)
      if (controller === pending) { controller = null; isBusy.value = false }
    }
  }
  function cancel(): void {
    if (!controller) return
    controller.abort(); controller = null; isBusy.value = false
    error.value = '已停止等待，本次未展示分析。'
  }
  function clear(): void { cancel(); submitted.value = null; response.value = null; error.value = null; validationError.value = null }
  async function retry(): Promise<void> {
    if (!submitted.value || isBusy.value) return
    repository.value = submitted.value.repository; issue.value = submitted.value.issue; topK.value = submitted.value.top_k
    await analyze()
  }
  return { repository, repositories, issue, topK, response, submitted, error, validationError, isBusy, analyze, cancel, clear, retry }
})
