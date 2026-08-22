/**
 * 채팅 상태 관리 Zustand Store (V2)
 *
 * 설계 원칙:
 * 1. 단일 진입점: switchThread() 하나로 스레드 전환 처리
 * 2. AbortController: 스트리밍 취소 완벽 지원
 * 3. 관심사 분리: SSE 처리는 chatStreamService로 위임
 * 4. Source of Truth: store.threadId가 현재 활성 스레드
 */

import { create } from 'zustand'
import { devtools } from 'zustand/middleware'
import { httpClient } from '@/shared/services'
import {
  regenerateStream,
} from '@/shared/services/chatStreamService'
import type { Message, Source, ThinkingStep } from '@/shared/types'
import type { ReasoningPreference } from '@/shared/types'

// ============================================================
// Types
// ============================================================

interface StreamingState {
  isStreaming: boolean
  currentMessage: string
  thinkingSteps: ThinkingStep[]
  sources: Source[]
  searchQueries: string[]
}

interface ChatState {
  // 현재 스레드
  threadId: string | null
  messages: Message[]

  // 스트리밍 상태
  streaming: StreamingState

  // 로딩/에러
  isLoading: boolean
  error: Error | null

  // 스레드 생성 중 (중복 생성 방지)
  isCreatingThread: boolean

  // 스트리밍 취소용
  abortController: AbortController | null

  // 폴링
  pollingInterval: ReturnType<typeof setInterval> | null
}

interface ChatActions {
  // 핵심 액션: 스레드 전환 (URL 변경 시 호출)
  switchThread: (threadId: string | null, initialMessages?: Message[]) => void

  // 메시지 전송
  regenerate: (mode: string, reasoning: ReasoningPreference, allowRemote: boolean) => Promise<void>

  // 스트리밍 제어
  cancelStreaming: () => void

  // v1.0.0: SSE 스트리밍용 외부 접근 가능 액션
  setStreamingContent: (content: string) => void
  appendStreamingContent: (token: string) => void
  addThinkingStep: (step: ThinkingStep) => void
  setSources: (sources: Source[]) => void
  finishStreaming: (content: string, metadata?: Record<string, unknown>, messageId?: string) => void
  startStreamingMode: () => void

  // 내부 헬퍼 (스트리밍 콜백용)
  _appendToken: (token: string) => void
  _addThinkingStep: (step: ThinkingStep) => void
  _addSource: (source: Source) => void
  _setSources: (sources: Source[]) => void
  _setSearchQueries: (queries: string[]) => void
  _finishStreaming: (finalMessage: Message) => void
  _startStreaming: () => AbortController
  _setThreadId: (threadId: string) => void

  // 폴링
  _startPolling: (threadId: string) => void
  _stopPolling: () => void
}

export type ChatStore = ChatState & ChatActions

// ============================================================
// Constants
// ============================================================

const initialStreamingState: StreamingState = {
  isStreaming: false,
  currentMessage: '',
  thinkingSteps: [],
  sources: [],
  searchQueries: [],
}

// ============================================================
// Store
// ============================================================

export const useChatStore = create<ChatStore>()(
  devtools(
    (set, get) => ({
      // --------------------------------------------------------
      // Initial State
      // --------------------------------------------------------
      threadId: null,
      messages: [],
      streaming: { ...initialStreamingState },
      isLoading: false,
      error: null,
      isCreatingThread: false,
      abortController: null,
      pollingInterval: null,

      // --------------------------------------------------------
      // 핵심 액션: 스레드 전환
      // --------------------------------------------------------
      switchThread: (threadId, initialMessages = []) => {
        const { abortController, pollingInterval, threadId: currentThreadId } = get()

        // 같은 스레드면 스킵 (메시지가 있는 경우)
        if (currentThreadId === threadId && get().messages.length > 0) {
          return
        }

        // 1. 진행 중인 스트리밍 취소
        if (abortController) {
          abortController.abort()
        }

        // 2. 폴링 중지
        if (pollingInterval) {
          clearInterval(pollingInterval)
        }

        // 3. 상태 초기화 및 새 스레드 설정
        set({
          threadId,
          messages: initialMessages,
          streaming: { ...initialStreamingState },
          isLoading: false,
          error: null,
          abortController: null,
          pollingInterval: null,
        }, false, 'switchThread')

        // 4. in-progress 상태 메시지가 있으면 폴링 시작
        if (threadId) {
          const IN_PROGRESS_STATUSES = ['queued', 'analyzing', 'searching', 'thinking', 'generating']
          const hasInProgress = initialMessages.some(
            m => m.role === 'assistant' && IN_PROGRESS_STATUSES.includes(m.status || '')
          )
          if (hasInProgress) {
            get()._startPolling(threadId)
          }
        }
      },

      // --------------------------------------------------------
      // 메시지 전송
      // --------------------------------------------------------
      regenerate: async (mode, reasoning, allowRemote) => {
        const { threadId, messages, _startStreaming, _appendToken, setStreamingContent, _addThinkingStep, _addSource, _setSources, _setSearchQueries, _finishStreaming } = get()
        if (!threadId) return

        // 마지막 assistant 메시지 제거
        const lastAssistantIdx = messages.reduce((lastIdx: number, m: Message, idx: number) => m.role === 'assistant' ? idx : lastIdx, -1)
        if (lastAssistantIdx === -1) return

        const newMessages = messages.slice(0, lastAssistantIdx)
        const removedAssistant = messages[lastAssistantIdx]
        let accepted = false
        set({ messages: newMessages }, false, 'removeLastAssistant')

        const abortController = _startStreaming()

        try {
          await regenerateStream(threadId, mode, reasoning, allowRemote, {
            onAccepted: () => { accepted = true },
            onToken: _appendToken,
            onContent: setStreamingContent,
            onThinkingStep: _addThinkingStep,
            onSource: _addSource,
            onSources: _setSources,
            onSearchQueries: _setSearchQueries,
            onComplete: _finishStreaming,
            onError: (err) => {
              set({ error: err, isLoading: false }, false, 'regenerateError')
            },
          }, abortController.signal)
        } catch (err) {
          if (!accepted && get().threadId === threadId) {
            set({ messages: [...newMessages, removedAssistant] }, false, 'restoreLastAssistant')
          }
          if (err instanceof DOMException && err.name === 'AbortError') {
            return
          }
          set({ error: err as Error, isLoading: false }, false, 'regenerateError')
        }
      },

      // --------------------------------------------------------
      // 스트리밍 제어
      // --------------------------------------------------------
      cancelStreaming: () => {
        const { abortController } = get()
        if (abortController) {
          abortController.abort()
        }
        set({
          streaming: { ...initialStreamingState },
          isLoading: false,
          abortController: null,
        }, false, 'cancelStreaming')
      },

      // --------------------------------------------------------
      // v1.0.0: SSE 스트리밍용 외부 접근 가능 액션
      // ChatPage에서 EventSource 이벤트 처리 시 호출
      // --------------------------------------------------------
      setStreamingContent: (content) => set((state) => ({
        streaming: {
          ...state.streaming,
          currentMessage: content,
        },
      }), false, 'setStreamingContent'),

      appendStreamingContent: (token) => set((state) => ({
        streaming: {
          ...state.streaming,
          currentMessage: state.streaming.currentMessage + token,
        },
      }), false, 'appendStreamingContent'),

      addThinkingStep: (step) => set((state) => ({
        streaming: {
          ...state.streaming,
          thinkingSteps: [...state.streaming.thinkingSteps, step],
        },
      }), false, 'addThinkingStep'),

      setSources: (sources) => set((state) => ({
        streaming: {
          ...state.streaming,
          sources,
        },
      }), false, 'setSources'),

      finishStreaming: (content, metadata = {}, messageId) => set((state) => {
        const completedMessage: Message = {
          message_id: messageId,
          role: 'assistant' as const,
          content,
          timestamp: Date.now() / 1000,
          status: 'completed' as const,
          metadata: metadata as Message['metadata'],
        }

        const messageIndex = messageId
          ? state.messages.findIndex((message) => message.message_id === messageId)
          : -1

        return {
          messages: messageIndex >= 0
            ? state.messages.map((message, index) =>
                index === messageIndex ? { ...message, ...completedMessage } : message
              )
            : [...state.messages, completedMessage],
          streaming: { ...initialStreamingState },
          isLoading: false,
          abortController: null,
        }
      }, false, 'finishStreaming'),

      startStreamingMode: () => set({
        streaming: {
          isStreaming: true,
          currentMessage: '',
          thinkingSteps: [],
          sources: [],
          searchQueries: [],
        },
        isLoading: true,
        error: null,
      }, false, 'startStreamingMode'),

      // --------------------------------------------------------
      // 내부 헬퍼 (스트리밍 콜백용)
      // --------------------------------------------------------
      _startStreaming: () => {
        const abortController = new AbortController()
        set({
          streaming: {
            isStreaming: true,
            currentMessage: '',
            thinkingSteps: [],
            sources: [],
            searchQueries: [],
          },
          isLoading: true,
          error: null,
          abortController,
        }, false, 'startStreaming')
        return abortController
      },

      _appendToken: (token) => set((state) => ({
        streaming: {
          ...state.streaming,
          currentMessage: state.streaming.currentMessage + token,
        },
      }), false, 'appendToken'),

      _addThinkingStep: (step) => set((state) => ({
        streaming: {
          ...state.streaming,
          thinkingSteps: [...state.streaming.thinkingSteps, step],
        },
      }), false, 'addThinkingStep'),

      _addSource: (source) => set((state) => ({
        streaming: {
          ...state.streaming,
          sources: [...state.streaming.sources, source],
        },
      }), false, 'addSource'),

      _setSources: (sources) => set((state) => ({
        streaming: {
          ...state.streaming,
          sources,
        },
      }), false, 'setSources'),

      _setSearchQueries: (queries) => set((state) => ({
        streaming: {
          ...state.streaming,
          searchQueries: queries,
        },
      }), false, 'setSearchQueries'),

      _finishStreaming: (finalMessage) => set((state) => ({
        messages: [...state.messages, finalMessage],
        streaming: { ...initialStreamingState },
        isLoading: false,
        abortController: null,
      }), false, 'finishStreaming'),

      _setThreadId: (threadId) => set({ threadId }, false, 'setThreadIdFromStream'),

      // --------------------------------------------------------
      // 폴링 (백그라운드 생성 상태 확인)
      // --------------------------------------------------------
      _startPolling: (threadId) => {
        const { pollingInterval, _stopPolling } = get()
        if (pollingInterval) _stopPolling()

        const interval = setInterval(async () => {
          try {
            const response = await httpClient.get<{ messages: Message[] }>(`/threads/${threadId}`)
            const currentState = get()

            // 스레드가 변경되었으면 폴링 중지
            if (currentState.threadId !== threadId) {
              currentState._stopPolling()
              return
            }

            const IN_PROGRESS_STATUSES = ['queued', 'analyzing', 'searching', 'thinking', 'generating']
            const serverInProgress = response.messages.find(
              m => m.role === 'assistant' && IN_PROGRESS_STATUSES.includes(m.status || '')
            )

            if (!serverInProgress) {
              // 모든 메시지 처리 완료
              set({ messages: response.messages, isLoading: false }, false, 'pollingComplete')
              currentState._stopPolling()
            }
          } catch (err) {
            console.error('[chatStore] Polling failed:', err)
          }
        }, 3000)

        set({ pollingInterval: interval }, false, 'startPolling')
      },

      _stopPolling: () => {
        const { pollingInterval } = get()
        if (pollingInterval) {
          clearInterval(pollingInterval)
          set({ pollingInterval: null }, false, 'stopPolling')
        }
      },
    }),
    { name: 'chat-store' }
  )
)

// ============================================================
// Selector Hooks (성능 최적화)
// ============================================================

export const useChatMessages = () => useChatStore((state) => state.messages)
export const useChatStreaming = () => useChatStore((state) => state.streaming)
export const useChatThreadId = () => useChatStore((state) => state.threadId)
export const useChatIsLoading = () => useChatStore((state) => state.isLoading)

// ============================================================
// 하위 호환성 (기존 API 유지)
// ============================================================

// setThread와 clearThread는 switchThread로 대체되지만, 하위 호환성 유지
export const useChatActions = () => {
  const store = useChatStore()
  return {
    setThread: (threadId: string, messages: Message[]) => store.switchThread(threadId, messages),
    clearThread: () => store.switchThread(null),
    regenerate: store.regenerate,
    cancelStreaming: store.cancelStreaming,
  }
}
