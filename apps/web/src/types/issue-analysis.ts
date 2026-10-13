import type { CodeChatCitation, CodeChatResponse, CodeChatSource } from '@/types/code-chat'

export interface IssueAnalysisRequest { repository: string; issue: string; top_k: number }
export interface IssueAnalysisResponse {
  repository: string; issue: string; summary: string
  related_code: CodeChatCitation[]; possible_call_chain: CodeChatCitation[]
  analysis: string[]; modification_suggestions: string[]; test_suggestions: string[]; risks: string[]
  source_ids: string[]; sources: CodeChatSource[]; status: 'analyzed' | 'insufficient_evidence'; model: string | null
  retrieval: CodeChatResponse['retrieval']; generation: CodeChatResponse['generation']
}
