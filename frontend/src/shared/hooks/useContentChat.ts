/**
 * 콘텐츠용 채팅 훅
 * - 첫 메시지: 스레드 생성 + 메시지 queued → SSE 스트리밍
 * - 이후 메시지: 기존 스레드에 메시지 추가 → SSE 스트리밍
 * - v1.0.0: POST → message_id → EventSource 패턴 사용
 * - v1.1.0: 콘텐츠 재방문 시 이전 스레드 자동 복원 + source_options 저장
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import { toast } from 'sonner'
import {
  createThreadStream,
  regenerateStream as requestRegenerateStream,
  sendMessageStream,
  type AcceptedMessage,
  type StreamingCallbacks,
} from '@/shared/services/chatStreamService'
import { getThreads, updateThreadMetadata } from '@/shared/services/endpoints/threads'
import type { SearchSource, ThinkingStep, AIMode } from '@/features/chat/types'
import type { ReasoningPreference } from '@/shared/types'

interface ContentMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp?: number
  metadata?: {
    sources?: SearchSource[]
    thinking_steps?: ThinkingStep[]
    mode?: AIMode
  }
}

interface StreamingMetadata {
  currentMessage: string
  thinkingSteps: ThinkingStep[]
  sources: SearchSource[]
}

export interface ContentSourceOptions {
  include_summary: boolean
  include_transcription: boolean
  speaker_filter: string[] | null
  selected_content_ids?: string[]
  include_all_docs?: boolean
  include_web_search?: boolean
}

export function useContentChat(
  contentId: string,
  _contentTitle: string,
  sourceOptions?: ContentSourceOptions,
  onRestoreOptions?: (options: ContentSourceOptions) => void
) {
  const [messages, setMessages] = useState<ContentMessage[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isInitializing, setIsInitializing] = useState(true)
  const [hasExistingThread, setHasExistingThread] = useState(false)
  const threadIdRef = useRef<string | null>(null)
  const activeStreamRef = useRef<AbortController | null>(null)
  // 복원 직후 source_options 변경을 메타데이터 저장에서 skip하기 위한 플래그
  const skipNextMetadataSaveRef = useRef(false)

  const [streamingMetadata, setStreamingMetadata] = useState<StreamingMetadata>({
    currentMessage: '',
    thinkingSteps: [],
    sources: [],
  })

  // 마운트 시: 이전 스레드 복원
  useEffect(() => {
    let cancelled = false

    async function loadExistingThread() {
      try {
        threadIdRef.current = null
        setHasExistingThread(false)
        setMessages([])
        setIsLoading(false)
        setStreamingMetadata({ currentMessage: '', thinkingSteps: [], sources: [] })
        setIsInitializing(true)
        const response = await getThreads({ content_id: contentId, limit: 1 })
        if (cancelled || response.threads.length === 0) return

        const latest = response.threads[0]

        // 메시지가 없는 빈 스레드는 건너뜀
        if (!latest.messages || latest.messages.length === 0) return

        threadIdRef.current = latest.thread_id
        setHasExistingThread(true)

        // 메시지 복원
        const restoredMessages: ContentMessage[] = latest.messages.map((m) => ({
          role: m.role as 'user' | 'assistant',
          content: m.content,
          timestamp: m.timestamp,
          metadata: m.metadata as ContentMessage['metadata'],
        }))
        setMessages(restoredMessages)

        // metadata에서 source_options 복원 → 부모에 알림
        const savedOptions = latest.metadata?.source_options as ContentSourceOptions | undefined
        if (savedOptions && onRestoreOptions) {
          skipNextMetadataSaveRef.current = true
          onRestoreOptions(savedOptions)
        }
      } catch (err) {
        console.warn('[useContentChat] 기존 스레드 로드 실패:', err)
      } finally {
        if (!cancelled) {
          setIsInitializing(false)
        }
      }
    }

    loadExistingThread()
    return () => {
      cancelled = true
    }
    // contentId가 바뀔 때만 재실행 (onRestoreOptions ref 불안정 제외)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contentId])

  useEffect(() => {
    return () => {
      activeStreamRef.current?.abort()
      activeStreamRef.current = null
    }
  }, [contentId])

  // source_options 변경 시 thread metadata 저장
  useEffect(() => {
    if (!threadIdRef.current || !sourceOptions) return

    // 복원 직후 첫 변경은 저장하지 않음 (무한루프 방지)
    if (skipNextMetadataSaveRef.current) {
      skipNextMetadataSaveRef.current = false
      return
    }

    updateThreadMetadata(threadIdRef.current, {
      source_options: sourceOptions as unknown as Record<string, unknown>,
    }).catch((err) =>
      console.warn('[useContentChat] thread metadata 저장 실패:', err)
    )
  }, [sourceOptions])

  const createStreamCallbacks = useCallback(
    (
      onAccepted?: (message: AcceptedMessage) => void
    ): StreamingCallbacks => ({
      onAccepted,
      onToken: (token) => setStreamingMetadata((prev) => ({
        ...prev,
        currentMessage: prev.currentMessage + token,
      })),
      onContent: (content) => setStreamingMetadata((prev) => ({
        ...prev,
        currentMessage: content,
      })),
      onThinkingStep: (step) => setStreamingMetadata((prev) => ({
        ...prev,
        thinkingSteps: [...prev.thinkingSteps, step],
      })),
      onSource: (source) => setStreamingMetadata((prev) => ({
        ...prev,
        sources: [...prev.sources, source],
      })),
      onSources: (sources) => setStreamingMetadata((prev) => ({
        ...prev,
        sources,
      })),
      onSearchQueries: () => {},
      onComplete: (message) => {
        setMessages((prev) => [...prev, {
          role: 'assistant',
          content: message.content,
          timestamp: message.timestamp,
          metadata: message.metadata as ContentMessage['metadata'],
        }])
        setIsLoading(false)
        setStreamingMetadata({ currentMessage: '', thinkingSteps: [], sources: [] })
      },
      onError: () => {},
    }),
    []
  )

  const sendMessage = useCallback(
    async (content: string, _mode: string, reasoning: ReasoningPreference, allowRemote: boolean) => {
      // sourceOptions에서 mode 자동 결정 (ChatArea의 mode 파라미터 무시)
      const effectiveMode = sourceOptions?.include_web_search ? 'hybrid' : 'rag'

      const userMessage: ContentMessage = {
        role: 'user',
        content,
        timestamp: Date.now() / 1000,
      }

      setMessages((prev) => [...prev, userMessage])
      setIsLoading(true)
      setStreamingMetadata({
        currentMessage: '',
        thinkingSteps: [],
        sources: [],
      })
      activeStreamRef.current?.abort()
      const abortController = new AbortController()
      activeStreamRef.current = abortController

      try {
        const msgContext: Record<string, unknown> = { content_id: contentId }
        if (sourceOptions) {
          msgContext.source_options = sourceOptions
          if (sourceOptions.selected_content_ids?.length) {
            msgContext.content_ids = sourceOptions.selected_content_ids
          }
          msgContext.search_scope = sourceOptions.include_all_docs ? 'all' : 'selected'
        }

        const request = {
          query: content,
          mode: effectiveMode,
          reasoning,
          allow_remote: allowRemote,
          context: msgContext,
        }
        if (!threadIdRef.current) {
          await createThreadStream(
            request,
            createStreamCallbacks(({ thread_id }) => {
              threadIdRef.current = thread_id
              setHasExistingThread(true)
            }),
            abortController.signal
          )
        } else {
          await sendMessageStream(
            threadIdRef.current,
            request,
            createStreamCallbacks(),
            abortController.signal
          )
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return
        const error = err as Error
        setIsLoading(false)
        toast.error('메시지 전송 실패', { description: error.message })
      } finally {
        if (activeStreamRef.current === abortController) {
          activeStreamRef.current = null
        }
      }
    },
    [contentId, createStreamCallbacks, sourceOptions]
  )

  const regenerate = useCallback(
    async (_mode: string, reasoning: ReasoningPreference, allowRemote: boolean) => {
      if (!threadIdRef.current || messages.length === 0) return
      if (messages[messages.length - 1].role !== 'assistant') return

      // sourceOptions에서 mode 자동 결정
      const effectiveMode = sourceOptions?.include_web_search ? 'hybrid' : 'rag'

      const removedAssistant = messages[messages.length - 1]
      const regenerationThreadId = threadIdRef.current
      setMessages((prev) => prev.slice(0, -1))
      setIsLoading(true)
      setStreamingMetadata({
        currentMessage: '',
        thinkingSteps: [],
        sources: [],
      })
      activeStreamRef.current?.abort()
      const abortController = new AbortController()
      activeStreamRef.current = abortController

      try {
        await requestRegenerateStream(
          regenerationThreadId,
          effectiveMode,
          reasoning,
          allowRemote,
          createStreamCallbacks(),
          abortController.signal
        )
      } catch (err) {
        const isCurrentRegeneration = (
          threadIdRef.current === regenerationThreadId &&
          activeStreamRef.current === abortController
        )
        if (isCurrentRegeneration) {
          setMessages((prev) => [...prev, removedAssistant])
          setIsLoading(false)
          setStreamingMetadata({ currentMessage: '', thinkingSteps: [], sources: [] })
        }
        if (err instanceof DOMException && err.name === 'AbortError') return
        const error = err as Error
        if (isCurrentRegeneration) {
          toast.error('재생성 실패', { description: error.message })
        }
      } finally {
        if (activeStreamRef.current === abortController) {
          activeStreamRef.current = null
        }
      }
    },
    [createStreamCallbacks, messages, sourceOptions]
  )

  // 새 대화 시작: threadIdRef 초기화 + 메시지 클리어
  const startNewThread = useCallback(() => {
    activeStreamRef.current?.abort()
    activeStreamRef.current = null
    threadIdRef.current = null
    setMessages([])
    setIsLoading(false)
    setStreamingMetadata({ currentMessage: '', thinkingSteps: [], sources: [] })
    setHasExistingThread(false)
  }, [])

  return {
    messages,
    isStreaming: isLoading,
    isInitializing,
    hasExistingThread,
    currentStreamingMessage: streamingMetadata.currentMessage,
    currentThinkingSteps: streamingMetadata.thinkingSteps,
    currentSources: streamingMetadata.sources,
    sendMessage,
    regenerate,
    startNewThread,
  }
}
