<script setup lang="ts">
import { ElRadioButton, ElRadioGroup, ElTag } from 'element-plus'
import { storeToRefs } from 'pinia'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import ChatWorkspace from '@/components/ChatWorkspace.vue'
import CodeChat from '@/components/CodeChat.vue'
import IssueAnalyzer from '@/components/IssueAnalyzer.vue'
import RetrievalDebug from '@/components/RetrievalDebug.vue'
import { useChatStore } from '@/stores/chat'
import { useCodeChatStore } from '@/stores/code-chat'
import { useIssueAnalysisStore } from '@/stores/issue-analysis'
import { useRetrievalStore } from '@/stores/retrieval'

type Workspace = 'llm' | 'retrieval' | 'code-chat' | 'issue-analysis'
function workspaceFromHash(): Workspace {
  return window.location.hash === '#issue-analysis' ? 'issue-analysis' : window.location.hash === '#code-chat' ? 'code-chat'
    : window.location.hash === '#retrieval' ? 'retrieval' : 'llm'
}
const activeWorkspace = ref<Workspace>(workspaceFromHash())
const { model } = storeToRefs(useChatStore())
const { response } = storeToRefs(useRetrievalStore())
const { model: codeChatModel } = storeToRefs(useCodeChatStore())
const { response: issueResponse } = storeToRefs(useIssueAnalysisStore())
const displayedModel = computed(() => activeWorkspace.value === 'retrieval'
  ? response.value?.embedding_model : activeWorkspace.value === 'code-chat' ? codeChatModel.value
    : activeWorkspace.value === 'issue-analysis' ? issueResponse.value?.model : model.value)

function syncWorkspace(): void {
  activeWorkspace.value = workspaceFromHash()
}

watch(activeWorkspace, (workspace) => {
  if (window.location.hash !== `#${workspace}`) window.location.hash = workspace
})
onMounted(() => window.addEventListener('hashchange', syncWorkspace))
onBeforeUnmount(() => window.removeEventListener('hashchange', syncWorkspace))
</script>

<template>
  <main class="app-shell">
    <section class="workspace-card" aria-label="RepoPilot 工作区">
      <header class="app-header">
        <div>
          <h1>RepoPilot</h1>
          <p>LLM · 代码检索 · 仓库问答 · Issue 分析</p>
        </div>
        <el-tag effect="plain" :type="displayedModel ? 'success' : 'info'">
          {{ activeWorkspace === 'retrieval' ? 'Embedding' : 'Model' }}:
          {{ displayedModel ?? (activeWorkspace === 'retrieval' ? '搜索后显示' : '等待连接') }}
        </el-tag>
      </header>
      <nav class="workspace-switch" aria-label="工作区切换">
        <el-radio-group v-model="activeWorkspace">
          <el-radio-button value="llm">LLM 工作区</el-radio-button>
          <el-radio-button value="retrieval">Retrieval Debug</el-radio-button>
          <el-radio-button value="code-chat">Code Chat</el-radio-button>
          <el-radio-button value="issue-analysis">Issue Analyzer</el-radio-button>
        </el-radio-group>
      </nav>
      <ChatWorkspace v-show="activeWorkspace === 'llm'" />
      <RetrievalDebug v-show="activeWorkspace === 'retrieval'" />
      <CodeChat v-show="activeWorkspace === 'code-chat'" />
      <IssueAnalyzer v-show="activeWorkspace === 'issue-analysis'" />
    </section>
  </main>
</template>
