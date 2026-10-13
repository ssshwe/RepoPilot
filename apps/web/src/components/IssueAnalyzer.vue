<script setup lang="ts">
import { ElButton, ElInput, ElOption, ElSelect, ElTag } from 'element-plus'
import { storeToRefs } from 'pinia'
import { computed } from 'vue'
import CitedSources from '@/components/CitedSources.vue'
import { useIssueAnalysisStore } from '@/stores/issue-analysis'

const store = useIssueAnalysisStore()
const { repository, repositories, issue, topK, response, submitted, error, validationError, isBusy } = storeToRefs(store)
const length = computed(() => Array.from(issue.value).length)
const sections = computed(() => response.value ? [
  { name: 'Analysis', items: response.value.analysis },
  { name: 'Modification Suggestions', items: response.value.modification_suggestions },
  { name: 'Test Suggestions', items: response.value.test_suggestions },
  { name: 'Risks', items: response.value.risks },
] : [])
const example = '商城退款接口可能存在重复退款问题，请分析应该重点检查哪些代码。'
</script>
<template>
  <div class="issue-workspace">
    <section class="mode-summary"><div><strong>Issue Analyzer · 问题分析</strong><span>检索相关代码，生成待核实的分析和建议。</span></div>
      <el-button text :disabled="!submitted && !validationError" @click="store.clear">清空分析</el-button></section>
    <form class="issue-form" aria-label="Issue 分析输入" novalidate @submit.prevent="store.analyze">
      <div class="code-chat-settings issue-settings">
        <div class="retrieval-field"><label for="issue-repository">Repository</label>
          <el-select id="issue-repository" v-model="repository" filterable allow-create default-first-option :disabled="isBusy">
            <el-option v-for="name in repositories" :key="name" :label="name" :value="name" />
          </el-select><span class="field-hint">新仓库名输入后按 Enter 确认。</span></div>
        <div class="retrieval-field"><label for="issue-top-k">TopK</label><input id="issue-top-k" v-model.number="topK"
          type="number" class="top-k-input" min="1" max="20" step="1" :disabled="isBusy" /></div>
      </div>
      <div class="issue-input"><label for="issue-text">Issue · 问题描述</label>
        <el-input id="issue-text" v-model="issue" type="textarea" :rows="3" resize="none" :maxlength="16384" :disabled="isBusy"
          placeholder="描述待检查的问题，例如同一订单重复请求退款。" @keydown.ctrl.enter.prevent="store.analyze" @keydown.meta.enter.prevent="store.analyze" />
        <p v-if="validationError" class="code-chat-validation" role="alert">{{ validationError }}</p>
        <div class="code-chat-compose-actions"><span class="field-hint">{{ length }} / 8192 字符 · Ctrl / ⌘ + Enter 分析</span>
          <div><el-button text :disabled="isBusy" @click="issue = example">使用示例</el-button>
            <el-button v-if="isBusy" @click="store.cancel">停止等待</el-button>
            <el-button type="primary" native-type="submit" :loading="isBusy" :disabled="isBusy">{{ isBusy ? '正在分析' : '分析 Issue' }}</el-button></div>
        </div>
      </div>
    </form>
    <section class="issue-results" aria-label="Issue 分析结果" :aria-busy="isBusy">
      <div v-if="isBusy" class="code-chat-pending" role="status">正在检索相关代码并生成结构化分析…</div>
      <div v-else-if="error" class="code-chat-error" role="alert"><strong>本次分析未完成</strong><p>{{ error }}</p>
        <el-button size="small" @click="store.retry">重试这个 Issue</el-button></div>
      <template v-else-if="response">
        <header class="issue-result-heading"><span>{{ response.repository }} · TopK {{ response.retrieval.top_k }}</span>
          <el-tag :type="response.status === 'analyzed' ? 'info' : 'warning'" effect="plain" size="small">
            {{ response.status === 'analyzed' ? '附带代码引用' : '证据不足' }}</el-tag></header>
        <p class="issue-submitted">{{ response.issue }}</p>
        <section><h2>Task Summary</h2><p>{{ response.summary }}</p></section>
        <section><h2>Related Code</h2><ul v-if="response.related_code.length" class="issue-related">
          <li v-for="c in response.related_code" :key="c.source_id"><strong>{{ c.source_id }}</strong> {{ c.path }}
            <span>{{ c.symbol ?? '无 Symbol' }} · Lines {{ c.start_line }}–{{ c.end_line }}</span></li></ul>
          <p v-else class="code-chat-note">当前没有可用的相关代码证据。</p></section>
        <section><h2>Possible Call Chain</h2><p class="code-chat-note">候选顺序，调用关系尚需结合代码核实。</p>
          <ol v-if="response.possible_call_chain.length" class="issue-chain"><li v-for="c in response.possible_call_chain" :key="c.source_id">
            <strong>{{ c.source_id }}</strong> {{ c.symbol ?? c.path }}<span>{{ c.path }} · {{ c.start_line }}–{{ c.end_line }}</span></li></ol>
          <p v-else class="code-chat-note">当前证据无法确定调用顺序。</p></section>
        <section v-for="section in sections" :key="section.name"><h2>{{ section.name }}</h2>
          <ul v-if="section.items.length"><li v-for="(item, i) in section.items" :key="i">{{ item }}</li></ul>
          <p v-else class="code-chat-note">当前没有可用内容。</p></section>
        <CitedSources :citations="response.related_code" :sources="response.sources" />
        <footer class="code-chat-result-footer"><span>{{ response.model ?? '未调用生成模型' }}</span>
          <span>{{ response.retrieval.retrieved_chunks }} 个检索块 · {{ response.retrieval.used_sources }} 个上下文片段</span>
          <span v-if="response.retrieval.budget_limited">上下文受预算限制</span></footer>
        <p class="code-chat-note">分析与建议需要人工核实；本功能不会修改仓库代码。</p>
      </template>
      <div v-else class="empty-state"><strong>从一个待检查的 Issue 开始</strong><span>输入问题，查看相关代码、分析和测试建议。</span></div>
    </section>
  </div>
</template>
