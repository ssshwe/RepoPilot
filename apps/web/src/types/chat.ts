export type MessageRole = 'system' | 'user' | 'assistant'

export interface ApiChatMessage {
  role: MessageRole
  content: string
}

export interface ChatResponse {
  message: ApiChatMessage
  model: string
}

export interface ChatStreamMetadata {
  model: string
}

export interface ChatStreamDelta {
  content: string
}

export type WorkspaceMode = 'chat' | 'stream' | 'analyze' | 'agent'

export type AnalysisDifficulty = 'easy' | 'medium' | 'hard'

export interface RequirementAnalysis {
  summary: string
  difficulty: AnalysisDifficulty
  tasks: string[]
  risks: string[]
}

export type ConversationMessageStatus =
  | 'complete'
  | 'pending'
  | 'streaming'
  | 'failed'

export interface ConversationMessage extends ApiChatMessage {
  id: string
  status: ConversationMessageStatus
}
