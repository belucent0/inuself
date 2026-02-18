/**
 * Chat Feature 타입 정의
 */

export type AIMode = 'auto' | 'simple' | 'search' | 'rag' | 'reasoning' | 'hybrid'

export interface SearchSource {
  position: number
  title: string
  url: string
  snippet: string
  engine?: string
  source?: 'web' | 'rag'
}

export interface ThinkingStep {
  step: string
  content: string
  timestamp?: number
}

export interface QueryAnalysis {
  original_query: string
  reformulated_query: string
  search_queries: string[]
  keywords: string[]
  search_focus: string
}

/**
 * 메시지 상태 타입 (서버와 동기화)
 */
export type MessageStatus = 'pending' | 'generating' | 'completed' | 'failed' | 'cancelled'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  mode?: AIMode
  sources?: SearchSource[]
  thinkingSteps?: ThinkingStep[]
  queryAnalysis?: QueryAnalysis
  isStreaming?: boolean
  /** 메시지 상태 (서버 동기화용) */
  status?: MessageStatus
  /** UI 표시용 현재 단계 */
  currentStep?: string
}

export interface AIModeConfig {
  label: string
  description: string
  color: string
  bgColor: string
}

export const AI_MODE_CONFIG: Record<AIMode, AIModeConfig> = {
  auto: {
    label: '자동 모델',
    description: 'AI가 질문 유형에 맞게 자동 판단',
    color: 'text-gray-500',
    bgColor: 'bg-gray-500/10',
  },
  simple: {
    label: '대화',
    description: '일반 AI 대화',
    color: 'text-slate-500',
    bgColor: 'bg-slate-500/10',
  },
  search: {
    label: '웹 검색',
    description: '실시간 웹 검색 기반 답변',
    color: 'text-blue-500',
    bgColor: 'bg-blue-500/10',
  },
  rag: {
    label: '내 문서',
    description: '저장된 콘텐츠에서 검색',
    color: 'text-green-500',
    bgColor: 'bg-green-500/10',
  },
  reasoning: {
    label: '추론',
    description: '단계별 논리적 분석',
    color: 'text-purple-500',
    bgColor: 'bg-purple-500/10',
  },
  hybrid: {
    label: '통합 검색',
    description: '웹 + 내 문서 통합 검색',
    color: 'text-amber-500',
    bgColor: 'bg-amber-500/10',
  },
}
