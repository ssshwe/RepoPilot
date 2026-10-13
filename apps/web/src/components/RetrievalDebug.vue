<script setup lang="ts">
import { ElAlert, ElButton, ElInput, ElOption, ElSelect, ElTag } from 'element-plus'
import { storeToRefs } from 'pinia'
import { computed } from 'vue'

import { useRetrievalStore } from '@/stores/retrieval'

const store = useRetrievalStore()
const { repository, repositories, query, topK, response, isBusy, error, parametersChanged } = storeToRefs(store)
const queryLength = computed(() => Array.from(query.value).length)
</script>

<template>
  <div class="retrieval-workspace">
    <section class="mode-summary retrieval-heading">
      <div>
        <strong>Retrieval Debug · 代码检索</strong>
        <span>按仓库寻找相关代码，查看相似度、Symbol 与切块方式。</span>
      </div>
      <el-button text :disabled="isBusy || (!response && !error)" @click="store.clear">
        清空检索结果
      </el-button>
    </section>
    <form class="retrieval-form" aria-label="代码检索参数" novalidate @submit.prevent="store.search">
      <div class="retrieval-field">
        <label for="retrieval-repository">Repository</label>
        <el-select
          id="retrieval-repository" v-model="repository" filterable allow-create default-first-option
          :disabled="isBusy" placeholder="选择或输入已索引的仓库名"
          aria-describedby="repository-hint"
        >
          <el-option v-for="name in repositories" :key="name" :label="name" :value="name" />
        </el-select>
        <span id="repository-hint" class="field-hint">可输入其他已索引仓库名，按 Enter 确认。</span>
      </div>
      <div class="retrieval-field top-k-field">
        <label for="retrieval-top-k">TopK</label>
        <input
          id="retrieval-top-k" v-model.number="topK" class="top-k-input"
          type="number" min="1" max="20" step="1"
          :disabled="isBusy" aria-describedby="top-k-hint"
        />
        <span id="top-k-hint" class="field-hint">1–20 个代码块</span>
      </div>
      <div class="retrieval-field query-field">
        <label for="retrieval-query">Query</label>
        <el-input
          id="retrieval-query" v-model="query" type="textarea" :rows="2" resize="none"
          :disabled="isBusy" :maxlength="16384" placeholder="例如：退款逻辑在哪里？"
          aria-describedby="query-hint" @keydown.ctrl.enter.prevent="store.search"
          @keydown.meta.enter.prevent="store.search"
        />
        <span id="query-hint" class="field-hint" :class="{ 'field-hint--error': queryLength > 8192 }">
          {{ queryLength }} / 8192 字符 · Ctrl / ⌘ + Enter 搜索
        </span>
      </div>
      <div class="retrieval-actions">
        <el-button type="primary" native-type="submit" :loading="isBusy" :disabled="isBusy">
          {{ isBusy ? '正在检索' : 'Search' }}
        </el-button>
        <el-button v-if="isBusy" @click="store.cancel">取消搜索</el-button>
        <span>分数反映向量相似度，不是正确率。</span>
      </div>
    </form>
    <el-alert
      v-if="error" class="error-alert" :title="error" type="error" show-icon
      :closable="false" role="alert"
    />
    <section class="retrieval-results" aria-label="检索结果" :aria-busy="isBusy">
      <div v-if="isBusy" class="empty-state" role="status" aria-live="polite">
        <strong>正在检索代码…</strong>
        <span>结果将在检索完成后显示。</span>
      </div>
      <div v-else-if="!response" class="empty-state" role="status">
        <strong>{{ error ? '搜索尚未完成' : '等待检索' }}</strong>
        <span>{{ error ? '修正参数或服务状态后，再次点击 Search。' : '选择已索引的仓库，输入你要寻找的代码逻辑。' }}</span>
      </div>
      <template v-else>
        <header class="retrieval-result-summary" aria-live="polite">
          <strong>找到 {{ response.results.length }} 个代码块 · 请求 TopK {{ response.top_k }}</strong>
          <p><b>{{ response.repository }}</b> · {{ response.query }}</p>
          <div class="retrieval-result-meta">
            <span>{{ response.embedding_model }}</span>
            <span>Dimension {{ response.embedding_dimension }}</span>
            <span>{{ response.retrieval_latency_ms.toFixed(1) }} ms</span>
          </div>
          <p v-if="parametersChanged" class="parameters-changed">参数已修改，点击 Search 更新结果。</p>
        </header>
        <div v-if="response.results.length === 0" class="retrieval-empty" role="status">
          <strong>没有检索结果</strong>
          <span>请确认仓库已建立索引，或尝试其他 Query。</span>
        </div>
        <article v-for="result in response.results" :key="result.chunk_id" class="retrieval-result">
          <header class="retrieval-result-heading">
            <strong class="retrieval-rank">#{{ result.rank }}</strong>
            <h2><span class="metadata-label">Path</span> {{ result.path }}</h2>
            <el-tag effect="plain" :title="String(result.score)">Score {{ result.score.toFixed(6) }}</el-tag>
          </header>
          <div class="retrieval-result-meta">
            <span>{{ result.language }}</span>
            <span>Lines {{ result.start_line }}–{{ result.end_line }}</span>
            <span>{{ result.repository }}</span>
          </div>
          <dl class="retrieval-chunk-metadata">
            <div><dt>Symbol</dt><dd>{{ result.symbol ?? '—（无 Symbol）' }}</dd></div>
            <div><dt>Node Type</dt><dd>{{ result.node_type ?? '—（文本切块）' }}</dd></div>
            <div><dt>Parent Symbol</dt><dd>{{ result.parent_symbol ?? '—' }}</dd></div>
            <div>
              <dt>Chunking Strategy</dt>
              <dd>
                <el-tag size="small" :type="result.chunking_strategy === 'ast' ? 'success' : 'info'" effect="plain">
                  {{ result.chunking_strategy }}
                </el-tag>
                <span class="chunking-version">{{ result.chunking_version }}</span>
              </dd>
            </div>
          </dl>
          <details class="code-preview" :open="result.rank === 1">
            <summary>Code Preview · {{ result.path }}</summary>
            <pre><code>{{ result.content }}</code></pre>
          </details>
        </article>
      </template>
    </section>
  </div>
</template>
