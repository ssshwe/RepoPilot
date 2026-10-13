import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { validateSearch } from '@/api/retrieval-contract'
import { searchCode } from '@/api/retrieval'
import type { RetrievalSearchRequest, RetrievalSearchResponse } from '@/types/retrieval'

export const useRetrievalStore = defineStore('retrieval', () => {
  const repository = ref('demo-repository')
  const repositories = ref(['demo-repository'])
  const query = ref('')
  const topK = ref<number | string>(5)
  const response = ref<RetrievalSearchResponse | null>(null)
  const isBusy = ref(false)
  const error = ref<string | null>(null)
  let controller: AbortController | null = null

  const parametersChanged = computed(() => response.value !== null && (
    response.value.repository !== repository.value.trim() || response.value.query !== query.value
    || response.value.top_k !== topK.value
  ))

  async function search(): Promise<void> {
    if (isBusy.value) return
    const request: RetrievalSearchRequest = {
      repository: repository.value.trim(), query: query.value,
      top_k: typeof topK.value === 'number' ? topK.value : Number.NaN,
    }
    error.value = validateSearch(request)
    if (error.value) return
    repository.value = request.repository
    if (!repositories.value.includes(request.repository)) repositories.value.push(request.repository)
    response.value = null
    isBusy.value = true
    const pending = new AbortController()
    controller = pending
    let timedOut = false
    const timeout = globalThis.setTimeout(() => {
      timedOut = true
      pending.abort()
      if (controller === pending) {
        controller = null
        isBusy.value = false
        error.value = '搜索超时，请检查服务后重试。'
      }
    }, 180_000)
    try {
      const result = await searchCode(request, pending.signal)
      if (controller !== pending || pending.signal.aborted) return
      response.value = result
    } catch (failure) {
      if (controller !== pending) return
      error.value = pending.signal.aborted
        ? (timedOut ? '搜索超时，请检查服务后重试。' : '搜索已取消，可以修改参数后重试。')
        : failure instanceof TypeError
          ? '无法连接检索 API，请确认后端已启动且 API 地址与跨域配置正确。'
          : failure instanceof Error ? failure.message : '搜索失败，请稍后重试。'
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
    error.value = '搜索已取消，可以修改参数后重试。'
  }
  function clear(): void {
    if (isBusy.value) return
    response.value = null
    error.value = null
  }
  return {
    repository, repositories, query, topK, response, isBusy, error, parametersChanged,
    search, cancel, clear,
  }
})
