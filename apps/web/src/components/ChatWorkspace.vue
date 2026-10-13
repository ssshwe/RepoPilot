<script setup lang="ts">
import {
  ElAlert,
  ElButton,
  ElInput,
  ElRadioButton,
  ElRadioGroup,
  ElTag,
} from 'element-plus'
import { storeToRefs } from 'pinia'
import { computed, nextTick, ref, watch } from 'vue'

import { useChatStore } from '@/stores/chat'
import type { WorkspaceMode } from '@/types/chat'

const chatStore = useChatStore()
const {
  activeMode,
  systemPrompt,
  messages,
  analysis,
  analyzedRequirement,
  isBusy,
  error,
} = storeToRefs(chatStore)

const draft = ref('')
const resultPanel = ref<HTMLElement | null>(null)

const modes: Array<{
  value: WorkspaceMode
  label: string
  title: string
  description: string
  placeholder: string
  action: string
}> = [
  {
    value: 'chat',
    label: '普通 Chat',
    title: '完整响应对话',
    description: '通过 /api/chat 一次性接收完整回答。',
    placeholder: '输入消息；Enter 发送，Shift + Enter 换行',
    action: '发送',
  },
  {
    value: 'stream',
    label: 'Streaming',
    title: '流式响应对话',
    description: '通过 SSE 实时显示模型输出，并保存完整消息。',
    placeholder: '输入消息；回答将逐步显示',
    action: '流式发送',
  },
  {
    value: 'analyze',
    label: 'Structured Output',
    title: '软件需求分析',
    description: '通过 /api/analyze 返回经过 Schema 验证的结构化结果。',
    placeholder: '输入需要分析的软件开发需求',
    action: '开始分析',
  },
  {
    value: 'agent',
    label: 'Tool Calling',
    title: '工具调用演示',
    description: '模型调用 get_repository_info 后，根据工具结果回答。',
    placeholder: '例如：当前项目使用什么技术栈？',
    action: '运行工具调用',
  },
]

const currentMode = computed(() => {
  const mode = modes.find((candidate) => candidate.value === activeMode.value)
  if (!mode) {
    throw new Error(`未知工作区模式：${activeMode.value}`)
  }
  return mode
})
const canSend = computed(() => !isBusy.value && Boolean(draft.value.trim()))
const hasCurrentResult = computed(() =>
  activeMode.value === 'analyze'
    ? Boolean(analysis.value || analyzedRequirement.value)
    : messages.value.length > 0,
)
const pendingLabel = computed(() =>
  activeMode.value === 'agent' ? '正在调用工具…' : '正在等待完整回答…',
)

async function submit(): Promise<void> {
  if (!canSend.value) {
    return
  }

  const content = draft.value
  draft.value = ''
  await chatStore.send(content)
}

watch(
  messages,
  async () => {
    await nextTick()
    if (resultPanel.value) {
      resultPanel.value.scrollTop = resultPanel.value.scrollHeight
    }
  },
  { deep: true },
)
</script>

<template>
  <div class="chat-workspace">
      <nav class="mode-switch" aria-label="功能模式">
        <el-radio-group v-model="activeMode" :disabled="isBusy">
          <el-radio-button
            v-for="mode in modes"
            :key="mode.value"
            :value="mode.value"
          >
            {{ mode.label }}
          </el-radio-button>
        </el-radio-group>
      </nav>

      <section class="mode-summary">
        <div>
          <strong>{{ currentMode.title }}</strong>
          <span>{{ currentMode.description }}</span>
        </div>
        <el-button
          text
          :disabled="isBusy || !hasCurrentResult"
          @click="chatStore.clearCurrent"
        >
          清空当前结果
        </el-button>
      </section>

      <section
        v-if="activeMode !== 'analyze'"
        class="settings-row"
        aria-label="对话设置"
      >
        <label for="system-prompt">System Message</label>
        <el-input
          id="system-prompt"
          v-model="systemPrompt"
          :disabled="isBusy"
          maxlength="20000"
          placeholder="设置模型行为"
        />
      </section>

      <el-alert
        v-if="error"
        class="error-alert"
        :title="error"
        type="error"
        show-icon
        closable
        @close="chatStore.dismissError"
      />

      <section
        v-if="activeMode === 'analyze'"
        ref="resultPanel"
        class="analysis-panel"
        aria-live="polite"
      >
        <div v-if="!analysis" class="empty-state">
          <strong>{{ isBusy ? '正在分析需求' : '等待软件需求' }}</strong>
          <span>
            {{
              isBusy
                ? '模型正在生成并验证结构化结果。'
                : '结果将分为摘要、难度、任务和风险。'
            }}
          </span>
        </div>

        <article v-else class="analysis-result">
          <div class="analysis-heading">
            <div>
              <span class="section-label">需求摘要</span>
              <h2>{{ analysis.summary }}</h2>
            </div>
            <el-tag
              effect="light"
              :type="
                analysis.difficulty === 'easy'
                  ? 'success'
                  : analysis.difficulty === 'medium'
                    ? 'warning'
                    : 'danger'
              "
            >
              {{ analysis.difficulty }}
            </el-tag>
          </div>

          <p v-if="analyzedRequirement" class="original-requirement">
            <strong>原始需求：</strong>{{ analyzedRequirement }}
          </p>

          <div class="analysis-grid">
            <section>
              <h3>实施任务</h3>
              <ol>
                <li v-for="task in analysis.tasks" :key="task">{{ task }}</li>
              </ol>
            </section>
            <section>
              <h3>主要风险</h3>
              <ul v-if="analysis.risks.length">
                <li v-for="risk in analysis.risks" :key="risk">{{ risk }}</li>
              </ul>
              <p v-else class="no-risks">暂未识别到明确风险。</p>
            </section>
          </div>
        </article>
      </section>

      <section
        v-else
        ref="resultPanel"
        class="message-list"
        aria-live="polite"
      >
        <div v-if="messages.length === 0" class="empty-state">
          <strong>{{ currentMode.title }}</strong>
          <span v-if="activeMode === 'agent'">
            试试询问“当前项目使用什么技术栈？”。
          </span>
          <span v-else>对话会携带完整的多轮消息上下文。</span>
        </div>

        <article
          v-for="message in messages"
          :key="message.id"
          class="message"
          :class="[`message--${message.role}`, `message--${message.status}`]"
        >
          <div class="message-meta">
            <strong>{{ message.role === 'user' ? 'User' : 'RepoPilot' }}</strong>
            <span v-if="message.status === 'streaming'">正在流式输出…</span>
            <span v-else-if="message.status === 'pending'">{{ pendingLabel }}</span>
            <span v-else-if="message.status === 'failed'">请求失败</span>
          </div>
          <p v-if="message.content" class="message-content">
            {{ message.content }}<span
              v-if="message.status === 'streaming'"
              class="stream-cursor"
            />
          </p>
          <p
            v-else-if="message.status === 'streaming'"
            class="message-content message-content--waiting"
          >
            正在等待第一个文本片段<span class="stream-cursor" />
          </p>
          <p
            v-else-if="message.status === 'pending'"
            class="message-content message-content--waiting"
          >
            {{ pendingLabel }}
          </p>
          <p v-else class="message-content message-content--failed">
            没有收到可保存的回答。
          </p>
        </article>
      </section>

      <footer class="composer">
        <el-input
          v-model="draft"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 6 }"
          resize="none"
          :disabled="isBusy"
          maxlength="20000"
          :placeholder="currentMode.placeholder"
          @keydown.enter.exact.prevent="submit"
        />
        <el-button
          type="primary"
          :loading="isBusy"
          :disabled="!canSend"
          @click="submit"
        >
          {{ isBusy ? '处理中' : currentMode.action }}
        </el-button>
      </footer>
  </div>
</template>
