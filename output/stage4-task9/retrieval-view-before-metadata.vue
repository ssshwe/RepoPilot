<script setup lang="ts">
import { ElAlert, ElButton, ElInput, ElOption, ElSelect, ElTag } from 'element-plus'
import { storeToRefs } from 'pinia'
import { computed } from 'vue'
import RetrievalCandidates from '@/components/RetrievalCandidates.vue'
import { useRetrievalDebugStore } from '@/stores/retrieval-debug'
import type { DebugCandidate } from '@/types/retrieval-debug'
const store = useRetrievalDebugStore()
const { repository, repositories, query, topK, mode, language, pathPrefix, response, snapshot, isBusy, operation, error, parametersChanged } = storeToRefs(store)
const queryLength = computed(() => Array.from(query.value).length)
const batches = computed(() => response.value ? Object.values(response.value.channels) : [])
const pool = computed(() => response.value?.fused?.candidates ?? batches.value[0]?.candidates ?? [])
const scoreName = computed(() => ({ cosine_similarity: 'Cosine similarity', bm25: 'BM25 score', rrf: 'RRF score' }[response.value?.score_type ?? 'cosine_similarity']))
const candidate = (id: string): DebugCandidate | undefined => pool.value.find(c => c.chunk_id === id)
const reason = (id: string) => response.value?.channels.exact?.reasons?.[id]
</script>

<template>
  <div class="retrieval-workspace">
    <section class="mode-summary retrieval-heading">
      <div><strong>Retrieval Debug · 代码检索</strong><span>查看代码从哪一路召回，如何融合，为什么进入最终结果。</span></div>
      <el-button text :disabled="isBusy || (!response && !snapshot && !error)" @click="store.clear">清空检索结果</el-button>
    </section>
    <form class="retrieval-form debug-form" aria-label="代码检索参数" novalidate @submit.prevent="store.search">
      <div class="retrieval-field">
        <label for="retrieval-repository">Repository</label>
        <el-select id="retrieval-repository" v-model="repository" filterable allow-create default-first-option :disabled="isBusy" placeholder="已索引仓库名">
          <el-option v-for="name in repositories" :key="name" :label="name" :value="name" />
        </el-select>
      </div>
      <div class="retrieval-field"><label for="retrieval-mode">检索模式</label>
        <select id="retrieval-mode" v-model="mode" class="top-k-input" :disabled="isBusy">
          <option value="dense">Dense · 语义</option><option value="bm25">BM25 · 关键词</option><option value="hybrid">Hybrid · RRF 融合</option>
        </select>
      </div>
      <div class="retrieval-field"><label for="retrieval-top-k">TopK</label>
        <input id="retrieval-top-k" v-model.number="topK" class="top-k-input" type="number" min="1" max="20" step="1" :disabled="isBusy" aria-describedby="top-k-hint" />
        <span id="top-k-hint" class="field-hint">1–20 个最终代码块</span>
      </div>
      <div class="retrieval-field"><label for="retrieval-language">Language</label>
        <input id="retrieval-language" v-model="language" class="top-k-input" :disabled="isBusy" placeholder="全部，例如 java" />
      </div>
      <div class="retrieval-field debug-path-field"><label for="retrieval-path-prefix">Path prefix</label>
        <input id="retrieval-path-prefix" v-model="pathPrefix" class="top-k-input" :disabled="isBusy" placeholder="例如 backend/service/" aria-describedby="prefix-hint" />
        <span id="prefix-hint" class="field-hint">字面前缀；目录边界使用末尾 /。</span>
      </div>
      <div class="retrieval-field query-field"><label for="retrieval-query">Query</label>
        <el-input id="retrieval-query" v-model="query" type="textarea" :rows="2" resize="none" :disabled="isBusy" :maxlength="16384"
          placeholder="例如 RefundService.refund，或：退款逻辑在哪里？" aria-describedby="query-hint"
          @keydown.ctrl.enter.prevent="store.search" @keydown.meta.enter.prevent="store.search" />
        <span id="query-hint" class="field-hint" :class="{ 'field-hint--error': queryLength > 8192 }">{{ queryLength }} / 8192 字符 · Ctrl / ⌘ + Enter 搜索</span>
      </div>
      <div class="debug-future-options"><label><input type="checkbox" disabled /> Rerank · 后续任务</label><label><input type="checkbox" disabled /> Query Expansion · 后续任务</label></div>
      <div class="retrieval-actions">
        <el-button type="primary" native-type="submit" :loading="isBusy && operation === 'search'" :disabled="isBusy">{{ operation === 'search' ? '正在检索' : 'Search' }}</el-button>
        <el-button :loading="isBusy && operation === 'snapshot'" :disabled="isBusy" @click="store.synchronize">同步检索快照</el-button>
        <el-button v-if="isBusy" @click="store.cancel">取消等待</el-button>
        <span>首次使用或索引更新后，从当前索引同步检索快照。</span>
      </div>
    </form>
    <el-alert v-if="error" class="error-alert" :title="error" type="error" show-icon :closable="false" role="alert" />
    <el-alert v-if="snapshot" class="error-alert" :title="`${snapshot.repository} 已同步 ${snapshot.chunks} 个 Chunk，可以开始检索。`" type="success" show-icon :closable="false" />
    <section class="retrieval-results" aria-label="检索结果" :aria-busy="isBusy">
      <div v-if="isBusy" class="empty-state" role="status" aria-live="polite"><strong>{{ operation === 'snapshot' ? '正在同步检索快照…' : '正在检索代码…' }}</strong><span>完成后显示本次请求的结果与诊断。</span></div>
      <div v-else-if="!response" class="empty-state" role="status"><strong>{{ error ? '搜索尚未完成' : '等待检索' }}</strong><span>选择已索引仓库与检索模式，输入问题。</span></div>
      <template v-else>
        <header class="retrieval-result-summary" aria-live="polite">
          <strong>{{ response.mode.toUpperCase() }} · {{ response.results.length }} 个最终结果 / TopK {{ response.top_k }}</strong>
          <p><b>{{ response.repository }}</b> · {{ response.query }}</p>
          <div class="retrieval-result-meta"><span>{{ response.embedding_model }} · {{ response.embedding_dimension }} 维</span><span>Collection {{ response.collection }}</span><span>{{ response.retrieval_latency_ms.toFixed(1) }} ms</span></div>
          <p class="debug-identity">索引版本（快照 SHA-256）：{{ response.index_revision }}</p>
          <div class="debug-stage-timings" aria-label="各阶段耗时">
            <span v-for="batch in batches" :key="batch.channel">{{ batch.channel.toUpperCase() }} {{ batch.retrieval_latency_ms.toFixed(2) }} ms</span>
            <span v-if="response.fused">RRF {{ response.timings.fusion_ms.toFixed(2) }} ms · k={{ response.rrf_k }}</span>
            <span>协调与校验 {{ response.timings.overhead_ms.toFixed(2) }} ms</span>
          </div>
          <p class="field-hint">{{ response.filters.language ?? '全部语言' }} · {{ response.filters.path_prefix ?? '全部路径' }} · 去重并集 {{ response.untruncated_union_count }} → {{ response.fused ? `融合池 ${pool.length}` : `候选池 ${pool.length}` }} → 最终 {{ response.results.length }}</p>
          <p class="field-hint">Cosine 衡量向量相似度，BM25 衡量词法相关性，Exact 为匹配档位，RRF 融合排名；分数均不代表答案可信度。</p>
          <p v-if="parametersChanged" class="parameters-changed">参数已修改，点击 Search 更新结果。</p>
        </header>
        <section class="debug-channel-section" aria-label="各通道候选与融合排名">
          <h2>召回与融合诊断</h2>
          <RetrievalCandidates v-for="batch in batches" :key="batch.channel" :batch="batch" :response="response" />
          <RetrievalCandidates v-if="response.fused" :batch="response.fused" :response="response" />
        </section>
        <h2 class="debug-final-heading">最终 TopK · {{ scoreName }}</h2>
        <div v-if="!response.results.length" class="retrieval-empty" role="status"><strong>没有检索结果</strong><span>当前仓库与过滤范围内没有候选，请查看通道诊断。</span></div>
        <article v-for="result in response.results" :key="result.chunk_id" class="retrieval-result">
          <header class="retrieval-result-heading"><strong class="retrieval-rank">#{{ result.rank }}</strong><h2>{{ result.path }}</h2><el-tag effect="plain" :title="String(result.score)">{{ scoreName }} {{ result.score.toFixed(8) }}</el-tag></header>
          <div class="retrieval-result-meta"><span>{{ result.language }} · Lines {{ result.start_line }}–{{ result.end_line }}</span><span>{{ result.symbol ?? '无 Symbol' }}</span><span>{{ result.chunking_strategy }} · {{ result.chunking_version }}</span></div>
          <div class="debug-provenance" aria-label="结果来源与排名">
            <span v-for="(rank, channel) in candidate(result.chunk_id)?.channel_ranks" :key="channel">{{ String(channel).toUpperCase() }} #{{ rank }} · {{ candidate(result.chunk_id)?.channel_scores[channel]?.toFixed(8) }}</span>
          </div>
          <p v-if="reason(result.chunk_id)" class="debug-match-reason">Exact {{ reason(result.chunk_id)!.strength === 'full' ? '完整匹配' : '部分匹配' }}：{{ reason(result.chunk_id)!.matched_value }} · 实际行 {{ reason(result.chunk_id)!.start_line }}–{{ reason(result.chunk_id)!.end_line }}</p>
          <details v-if="response.mode === 'hybrid'" class="debug-contributions"><summary>展开 RRF 贡献</summary><ul><li v-for="(part, channel) in response.rrf_contributions[result.chunk_id]" :key="channel">{{ channel }}：1 / (60 + {{ part.rank }}) = {{ part.contribution.toFixed(10) }} · 原始分数 {{ part.raw_score }}</li></ul></details>
          <p class="debug-identity">Chunk {{ result.chunk_id }}</p>
          <details class="code-preview" :open="result.rank === 1"><summary>Code Preview · {{ result.path }}</summary><pre><code>{{ result.content }}</code></pre></details>
        </article>
      </template>
    </section>
  </div>
</template>
