export const request = { repository: 'demo-repository', question: '退款逻辑在哪里？', top_k: 5 }
export function response(req = request) {
  return {
    repository: req.repository, question: req.question, answer: 'refund 检查订单编号。',
    source_ids: ['S1'], status: 'answered', model: 'unit-model',
    citations: [{ repository: req.repository, source_id: 'S1', path: 'refund.py', symbol: 'refund', start_line: 12, end_line: 15 }],
    sources: [{ source_id: 'S1', language: 'python', content: 'def refund(order_id):\n    if not order_id:\n        raise ValueError("required")\n    return True\n' }],
    retrieval: { top_k: req.top_k, retrieved_chunks: 5, used_sources: 5, context_characters: 2500,
      context_budget_characters: 16000, budget_limited: false, empty_reason: null,
      collection: 'unit-code', embedding_model: 'unit-embedding', embedding_dimension: 3, latency_ms: 10 },
    generation: { called: true, latency_ms: 20 },
  }
}
