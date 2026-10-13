<script setup lang="ts">
import { sourceLines } from '@/api/code-chat-contract'
import type { CodeChatCitation, CodeChatSource } from '@/types/code-chat'
defineProps<{ citations: CodeChatCitation[]; sources: CodeChatSource[] }>()
</script>
<template>
  <section class="code-chat-sources" aria-label="Issue 引用来源">
    <h2>Sources <span>{{ citations.length }}</span></h2>
    <p v-if="!citations.length" class="code-chat-note">本次没有代码引用。</p>
    <details v-for="(citation, index) in citations" :key="citation.source_id" class="code-chat-source" :open="index === 0">
      <summary><span class="code-chat-source-id">{{ citation.source_id }}</span><span class="code-chat-source-path">{{ citation.path }}</span>
        <span class="code-chat-source-range">Lines {{ citation.start_line }}–{{ citation.end_line }}</span></summary>
      <dl class="code-chat-source-meta"><div><dt>Symbol</dt><dd>{{ citation.symbol ?? '—（无 Symbol）' }}</dd></div>
        <div><dt>Language</dt><dd>{{ sources[index]!.language }}</dd></div></dl>
      <div class="code-chat-code" :aria-label="`Code Preview · ${citation.source_id} · ${citation.path}`" tabindex="0">
        <div v-for="(line, offset) in sourceLines(sources[index]!.content)" :key="offset" class="code-chat-code-line">
          <span class="code-chat-line-number" aria-hidden="true">{{ citation.start_line + offset }}</span><code>{{ line || ' ' }}</code>
        </div>
      </div>
    </details>
  </section>
</template>
