<script setup lang="ts">
import { ElButton, ElInput, ElOption, ElSelect, ElTag } from 'element-plus'
import { storeToRefs } from 'pinia'
import { computed, nextTick, ref, watch } from 'vue'

import { sourceLines } from '@/api/code-chat-contract'
import { useCodeChatStore } from '@/stores/code-chat'

const store = useCodeChatStore()
const { repository, repositories, question, topK, turns, isBusy, validationError } = storeToRefs(store)
const questionLength = computed(() => Array.from(question.value).length)
const messages = ref<HTMLElement | null>(null)
const example = '退款服务在哪里校验订单编号，然后把退款金额转回钱包？'
watch(() => [turns.value.length, turns.value[turns.value.length - 1]?.state], async () => {
  await nextTick()
  const panel = messages.value
  const latest = panel?.querySelector<HTMLElement>('.code-chat-turn:last-child')
  if (panel && latest) panel.scrollTo({
    top: panel.scrollTop + latest.getBoundingClientRect().top - panel.getBoundingClientRect().top,
    behavior: 'auto',
  })
})
</script>

<template>
  <div class="code-chat-workspace">
    <section class="mode-summary code-chat-heading">
      <div><strong>Code Chat · 仓库问答</strong><span>针对已索引的代码提问，查看答案和引用片段。</span></div>
      <el-button text :disabled="!turns.length && !validationError" @click="store.clear">清空问答</el-button>
    </section>
    <div class="code-chat-settings">
      <div class="retrieval-field">
        <label for="code-chat-repository">Repository</label>
        <el-select id="code-chat-repository" v-model="repository" filterable allow-create default-first-option
          :disabled="isBusy" placeholder="选择或输入已索引仓库" aria-describedby="code-chat-repo-hint">
          <el-option v-for="name in repositories" :key="name" :label="name" :value="name" />
        </el-select>
        <span id="code-chat-repo-hint" class="field-hint">其他仓库名输入后按 Enter 确认。</span>
      </div>
      <div class="retrieval-field">
        <label for="code-chat-top-k">TopK</label>
        <input id="code-chat-top-k" v-model.number="topK" type="number" class="top-k-input"
          min="1" max="20" step="1" :disabled="isBusy" aria-describedby="code-chat-top-k-hint" />
        <span id="code-chat-top-k-hint" class="field-hint">检索 1–20 个代码块</span>
      </div>
    </div>
    <section ref="messages" class="code-chat-messages" aria-label="代码问答记录" :aria-busy="isBusy">
      <div v-if="!turns.length" class="empty-state">
        <strong>从一个代码问题开始</strong><span>每个问题独立检索，引用片段可展开核对。</span>
        <button type="button" class="code-chat-example" @click="question = example">试试：{{ example }}</button>
      </div>
      <article v-for="turn in turns" :key="turn.id" class="code-chat-turn">
        <header class="code-chat-question-heading"><strong>Question</strong><span>{{ turn.request.repository }} · TopK {{ turn.request.top_k }}</span></header>
        <p class="code-chat-question">{{ turn.request.question }}</p>
        <div v-if="turn.state === 'pending'" class="code-chat-pending" role="status" aria-live="polite">正在检索代码并生成回答…</div>
        <div v-else-if="turn.error" class="code-chat-error" :role="turn.state === 'error' ? 'alert' : 'status'">
          <strong>{{ turn.state === 'error' ? '本次问答未完成' : '已停止等待' }}</strong>
          <p>{{ turn.error }}</p><el-button size="small" :disabled="isBusy" @click="store.retry(turn)">重试这个问题</el-button>
        </div>
        <template v-else-if="turn.response">
          <header class="code-chat-answer-heading">
            <strong>Answer</strong>
            <el-tag :type="turn.response.status === 'answered' ? 'info' : 'warning'" effect="plain" size="small">
              {{ turn.response.status === 'answered' ? '附带代码引用' : '证据不足' }}
            </el-tag>
          </header>
          <p class="code-chat-answer">{{ turn.response.answer }}</p>
          <p v-if="turn.response.status === 'insufficient_evidence'" class="code-chat-note">
            {{ turn.response.retrieval.empty_reason === 'no_results' ? '没有检索到当前仓库的代码。请确认索引或换个问题。'
              : turn.response.retrieval.empty_reason === 'budget_too_small' ? '当前上下文预算无法容纳代码片段。'
                : '模型没有提供可用引用，可以换一种问法。' }}
          </p>
          <section class="code-chat-sources" aria-label="引用来源">
            <h2>Sources <span>{{ turn.response.citations.length }}</span></h2>
            <p v-if="!turn.response.citations.length" class="code-chat-note">本次没有代码引用。</p>
            <details v-for="(citation, index) in turn.response.citations" :key="citation.source_id"
              class="code-chat-source" :open="index === 0">
              <summary><span class="code-chat-source-id">{{ citation.source_id }}</span>
                <span class="code-chat-source-path">{{ citation.path }}</span>
                <span class="code-chat-source-range">Lines {{ citation.start_line }}–{{ citation.end_line }}</span>
              </summary>
              <dl class="code-chat-source-meta"><div><dt>Symbol</dt><dd>{{ citation.symbol ?? '—（无 Symbol）' }}</dd></div>
                <div><dt>Language</dt><dd>{{ turn.response.sources[index]!.language }}</dd></div></dl>
              <div class="code-chat-code" :aria-label="`Code Preview · ${citation.source_id} · ${citation.path}`" tabindex="0">
                <div v-for="(line, offset) in sourceLines(turn.response.sources[index]!.content)" :key="offset" class="code-chat-code-line">
                  <span class="code-chat-line-number" aria-hidden="true">{{ citation.start_line + offset }}</span><code>{{ line || ' ' }}</code>
                </div>
              </div>
            </details>
          </section>
          <footer class="code-chat-result-footer">
            <span>{{ turn.response.model ?? '未调用生成模型' }}</span>
            <span>{{ turn.response.retrieval.retrieved_chunks }} 个检索块 · {{ turn.response.retrieval.used_sources }} 个上下文片段</span>
            <span v-if="turn.response.retrieval.budget_limited">上下文受预算限制</span>
          </footer>
          <p v-if="turn.response.citations.length" class="code-chat-note">引用坐标经过校验，答案仍需结合代码核对。</p>
        </template>
      </article>
    </section>
    <form class="code-chat-composer" aria-label="代码问答输入" novalidate @submit.prevent="store.ask">
      <label for="code-chat-question">Question · 你的问题</label>
      <el-input id="code-chat-question" v-model="question" type="textarea" :rows="3" resize="none"
        :disabled="isBusy" :maxlength="16384" placeholder="例如：退款逻辑在哪里，如何校验订单编号？"
        aria-describedby="code-chat-question-hint" @keydown.ctrl.enter.prevent="store.ask" @keydown.meta.enter.prevent="store.ask" />
      <p v-if="validationError" class="code-chat-validation" role="alert">{{ validationError }}</p>
      <div class="code-chat-compose-actions">
        <span id="code-chat-question-hint" class="field-hint" :class="{ 'field-hint--error': questionLength > 8192 }">{{ questionLength }} / 8192 字符 · Ctrl / ⌘ + Enter 提问</span>
        <div><el-button v-if="isBusy" @click="store.cancel">停止等待</el-button>
          <el-button type="primary" native-type="submit" :loading="isBusy" :disabled="isBusy">{{ isBusy ? '正在回答' : '提问' }}</el-button></div>
      </div>
    </form>
  </div>
</template>
