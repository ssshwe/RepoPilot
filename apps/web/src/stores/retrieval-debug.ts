import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { validateSearch } from '@/api/retrieval-contract'
import { validateDebugSearch } from '@/api/retrieval-debug-contract'
import { debugSearch, syncSnapshot } from '@/api/retrieval-debug'
import type { DebugMode, DebugRequest, DebugResponse, SnapshotResponse } from '@/types/retrieval-debug'

export const useRetrievalDebugStore = defineStore('retrieval-debug', () => {
  const repository = ref('demo-repository'), repositories = ref(['demo-repository']), query = ref('')
  const topK = ref<number | string>(5), mode = ref<DebugMode>('dense'), language = ref(''), pathPrefix = ref('')
  const response = ref<DebugResponse | null>(null), snapshot = ref<SnapshotResponse | null>(null)
  const isBusy = ref(false), operation = ref<'search' | 'snapshot' | null>(null), error = ref<string | null>(null)
  let controller: AbortController | null = null
  const request = (): DebugRequest => ({ repository: repository.value.trim(), query: query.value,
    top_k: typeof topK.value === 'number' ? topK.value : Number.NaN, mode: mode.value,
    filters: { language: language.value || null, path_prefix: pathPrefix.value || null }, rerank: false, query_expansion: false })
  const parametersChanged = computed(() => {
    if (!response.value) return false
    const current = request(), old = response.value
    return old.repository !== current.repository || old.query !== current.query || old.top_k !== current.top_k
      || old.mode !== current.mode || JSON.stringify(old.filters) !== JSON.stringify(current.filters)
  })
  async function run(action: 'search' | 'snapshot'): Promise<void> {
    if (isBusy.value) return
    const captured = request()
    error.value = action === 'search' ? validateDebugSearch(captured)
      : validateSearch({ repository: captured.repository, query: 'snapshot', top_k: 5 })
    if (error.value) return
    repository.value = captured.repository
    if (!repositories.value.includes(captured.repository)) repositories.value.push(captured.repository)
    response.value = null; snapshot.value = null; isBusy.value = true; operation.value = action
    const pending = new AbortController(); controller = pending
    const timeout = globalThis.setTimeout(() => {
      pending.abort()
      if (controller === pending) { controller = null; isBusy.value = false; operation.value = null; error.value = '检索操作超时，请检查服务后重试。' }
    }, 180_000)
    try {
      const result = action === 'search' ? await debugSearch(captured, pending.signal) : await syncSnapshot(captured.repository, pending.signal)
      if (controller !== pending || pending.signal.aborted) return
      if (action === 'search') response.value = result as DebugResponse
      else snapshot.value = result as SnapshotResponse
    } catch (failure) {
      if (controller !== pending) return
      error.value = failure instanceof TypeError ? '无法连接检索 API，请确认后端、API 地址和跨域配置。'
        : failure instanceof Error ? failure.message : '检索操作失败，请稍后重试。'
    } finally {
      globalThis.clearTimeout(timeout)
      if (controller === pending) { controller = null; isBusy.value = false; operation.value = null }
    }
  }
  function cancel(): void {
    if (!controller) return
    controller.abort(); controller = null; isBusy.value = false; operation.value = null
    error.value = '已取消本次等待，可以修改参数后重试。'
  }
  function clear(): void { if (!isBusy.value) { response.value = null; snapshot.value = null; error.value = null } }
  return { repository, repositories, query, topK, mode, language, pathPrefix, response, snapshot, isBusy, operation, error,
    parametersChanged, search: () => run('search'), synchronize: () => run('snapshot'), cancel, clear }
})
