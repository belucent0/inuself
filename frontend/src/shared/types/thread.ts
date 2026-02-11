/**
 * Thread 관련 타입 정의
 *
 * 단일 소스 오브 트루스(Single Source of Truth)로 사용
 */

/**
 * 메시지 상태 타입
 * - pending: 전송 대기 중 (큐잉된 상태)
 * - generating: AI 응답 생성 중
 * - completed: 완료
 * - failed: 생성 실패
 * - cancelled: 취소됨
 */
export type MessageStatus = 'pending' | 'generating' | 'completed' | 'failed' | 'cancelled'

export interface Message {
  message_id?: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  metadata?: MessageMetadata
  status?: MessageStatus
}

export interface MessageMetadata {
  sources?: Source[]
  thinking_steps?: ThinkingStep[]
  mode?: string
  regenerated?: boolean
  [key: string]: unknown  // 추가 메타데이터 허용
}

export interface Source {
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
}

export interface Thread {
  thread_id: string
  title: string
  messages: Message[]
  created_at: number
  updated_at: number
  metadata?: Record<string, unknown>
}

export interface CreateThreadRequest {
  title?: string
  metadata?: Record<string, unknown>
}

export interface SendMessageRequest {
  content: string
  mode?: AIMode
  metadata?: Record<string, unknown>
}

export interface ThreadListResponse {
  threads: Thread[]
  total: number
}

/**
 * AI 모드 타입
 */
export type AIMode = 'auto' | 'simple' | 'search' | 'rag' | 'reasoning' | 'hybrid'

/**
 * SSE 이벤트 타입
 */
export type SSEEventType =
  | 'thinking'
  | 'source'
  | 'sources'
  | 'token'
  | 'content'
  | 'done'
  | 'error'
  | 'thread_created'
  | 'thread_id'
  | 'message_id'

export interface SSEEvent {
  type: SSEEventType
  data: unknown
}

export interface SSEThinkingEvent {
  type: 'thinking'
  data: ThinkingStep
}

export interface SSESourceEvent {
  type: 'source'
  data: Source
}

export interface SSESourcesEvent {
  type: 'sources'
  data: Source[]
}

export interface SSETokenEvent {
  type: 'token'
  data: string
}

export interface SSEContentEvent {
  type: 'content'
  data: string
}

export interface SSEDoneEvent {
  type: 'done'
  data: null
}

export interface SSEErrorEvent {
  type: 'error'
  data: string
}

export interface SSEThreadCreatedEvent {
  type: 'thread_created'
  data: {
    thread_id: string
    title: string
  }
}
