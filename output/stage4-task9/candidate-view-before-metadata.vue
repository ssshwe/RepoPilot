<script setup lang="ts">
import type { DebugBatch, DebugResponse } from '@/types/retrieval-debug'
const props = defineProps<{ batch: DebugBatch; response: DebugResponse }>()
const label = (name: string) => ({ dense: 'Cosine similarity', bm25: 'BM25 score', exact: 'Exact match tier', rrf: 'RRF score' }[name] ?? name)
const finalRank = (id: string) => props.response.results.find(r => r.chunk_id === id)?.rank ?? '—'
const fusedRank = (id: string) => props.response.fused?.candidates.find(c => c.chunk_id === id)?.channel_ranks.rrf ?? '—'
</script>

<template>
  <details class="debug-candidates">
    <summary>{{ batch.channel.toUpperCase() }} · {{ batch.candidates.length }} / {{ batch.candidate_limit }} 个候选 · {{ label(batch.channel) }}</summary>
    <p v-if="batch.channel === 'exact'" class="field-hint">定位规则：{{ batch.match ? `${batch.match.kind} · ${batch.match.mode} · ${batch.match.value}` : '没有可识别的唯一定位信息' }}</p>
    <p v-if="!batch.candidates.length" class="field-hint">当前范围内没有该通道的候选。</p>
    <div v-else class="debug-table-scroll">
      <table class="debug-table">
        <caption>{{ batch.channel.toUpperCase() }} 候选、融合与最终排名</caption>
        <thead><tr><th>通道排名</th><th>{{ label(batch.channel) }}</th><th>融合排名</th><th>最终排名</th><th>代码位置与诊断</th></tr></thead>
        <tbody><tr v-for="candidate in batch.candidates" :key="candidate.chunk_id">
          <td>#{{ candidate.channel_ranks[batch.channel] }}</td>
          <td :title="String(candidate.channel_scores[batch.channel])">{{ candidate.channel_scores[batch.channel]?.toFixed(8) }}</td>
          <td>{{ fusedRank(candidate.chunk_id) }}</td><td>{{ finalRank(candidate.chunk_id) }}</td>
          <td class="debug-source-cell">
            <strong>{{ candidate.source_metadata.path }}:{{ candidate.source_metadata.start_line }}–{{ candidate.source_metadata.end_line }}</strong>
            <span>{{ candidate.source_metadata.symbol ?? '无 Symbol' }} · {{ candidate.source_metadata.language }}</span>
            <span class="debug-identity">Chunk {{ candidate.chunk_id }}</span>
            <span v-if="batch.reasons?.[candidate.chunk_id]" class="debug-match-reason">
              {{ batch.reasons[candidate.chunk_id]!.strength === 'full' ? '完整匹配' : '部分匹配' }} · {{ batch.reasons[candidate.chunk_id]!.kind }} ·
              {{ batch.reasons[candidate.chunk_id]!.matched_value }} · 实际行 {{ batch.reasons[candidate.chunk_id]!.start_line }}–{{ batch.reasons[candidate.chunk_id]!.end_line }}
            </span>
            <details class="code-preview"><summary>展开候选代码</summary><pre><code>{{ candidate.source_metadata.content }}</code></pre></details>
          </td>
        </tr></tbody>
      </table>
    </div>
  </details>
</template>
