/**
 * 콘텐츠용 채팅 훅
 * - 첫 메시지: 스레드 생성 + 메시지 queued → SSE 스트리밍
 * - 이후 메시지: 기존 스레드에 메시지 추가 → SSE 스트리밍
 * - v1.0.0: POST → message_id → EventSource 패턴 사용
 * - v1.1.0: 콘텐츠 재방문 시 이전 스레드 자동 복원 + source_options 저장
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import { toast } from 'sonner'
import { httpClient } from '@/shared/services'
import { getAccessToken } from '@/shared/services/authToken'
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

interface SSEChunk {
  type: string
  data: unknown
}

interface QueuedMessageResponse {
  thread_id: string
  message_id: string
  user_message_id: string
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

  const parseSSEChunk = (line: string): SSEChunk | null => {
    if (!line.startsWith('data: ')) return null
    const dataStr = line.slice(6).trim()
    if (dataStr === '[DONE]') return { type: 'done', data: null }
    try {
      return JSON.parse(dataStr) as SSEChunk
    } catch {
      return null
    }
  }

  // ReadableStream 기반 SSE 처리 (regenerate 엔드포인트용)
  const processSSEStream = useCallback(
    async (stream: ReadableStream<Uint8Array>, mode: string) => {
      const reader = stream.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullContent = ''
      let sources: SearchSource[] = []
      let thinkingSteps: ThinkingStep[] = []

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          const chunk = parseSSEChunk(line)
          if (!chunk) continue

          switch (chunk.type) {
            case 'thinking':
              thinkingSteps.push(chunk.data as ThinkingStep)
              setStreamingMetadata((prev) => ({
                ...prev,
                thinkingSteps: [...thinkingSteps],
              }))
              break
            case 'source':
              sources.push(chunk.data as SearchSource)
              setStreamingMetadata((prev) => ({
                ...prev,
                sources: [...sources],
              }))
              break
            case 'sources':
              sources = Array.isArray(chunk.data)
                ? (chunk.data as SearchSource[])
                : []
              setStreamingMetadata((prev) => ({
                ...prev,
                sources: [...sources],
              }))
              break
            case 'token': {
              const token = (chunk.data as string) || ''
              fullContent += token
              setStreamingMetadata((prev) => ({
                ...prev,
                currentMessage: fullContent,
              }))
              break
            }
            case 'content': {
              const contentData = chunk.data as
                | { content?: string }
                | string
              fullContent =
                typeof contentData === 'object' && contentData?.content
                  ? contentData.content
                  : (contentData as string) || ''
              setStreamingMetadata((prev) => ({
                ...prev,
                currentMessage: fullContent,
              }))
              break
            }
            case 'done': {
              const assistantMessage: ContentMessage = {
                role: 'assistant',
                content: fullContent,
                timestamp: Date.now() / 1000,
                metadata: { sources, thinking_steps: thinkingSteps, mode: mode as AIMode },
              }
              setMessages((prev) => [...prev, assistantMessage])
              setIsLoading(false)
              setStreamingMetadata({
                currentMessage: '',
                thinkingSteps: [],
                sources: [],
              })
              return
            }
            case 'error':
              throw new Error(chunk.data as string)
          }
        }
      }
    },
    []
  )

  // EventSource 기반 SSE 처리 (v1.0.0 queued flow용)
  const connectEventSource = useCallback(
    (threadId: string, messageId: string, mode: string): Promise<void> => {
      return new Promise((resolve, reject) => {
        const accessToken = getAccessToken()
        const streamUrl = `${httpClient.getBaseUrl()}/threads/${threadId}/messages/${messageId}/stream${
          accessToken ? `?access_token=${encodeURIComponent(accessToken)}` : ''
        }`

        const eventSource = new EventSource(streamUrl)
        let fullContent = ''
        let sources: SearchSource[] = []
        let thinkingSteps: ThinkingStep[] = []

        eventSource.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data)
            const eventType = data.type
            const eventData = data.data

            switch (eventType) {
              case 'thinking_step':
              case 'query_analysis':
                thinkingSteps = [...thinkingSteps, eventData as ThinkingStep]
                setStreamingMetadata((prev) => ({
                  ...prev,
                  thinkingSteps,
                }))
                break

              case 'sources':
                sources = eventData || []
                setStreamingMetadata((prev) => ({
                  ...prev,
                  sources,
                }))
                break

              case 'token':
                fullContent += eventData || ''
                setStreamingMetadata((prev) => ({
                  ...prev,
                  currentMessage: fullContent,
                }))
                break

              case 'content':
                fullContent = eventData || ''
                setStreamingMetadata((prev) => ({
                  ...prev,
                  currentMessage: fullContent,
                }))
                break

              case 'done': {
                const finalContent = eventData?.content || fullContent
                const assistantMessage: ContentMessage = {
                  role: 'assistant',
                  content: finalContent,
                  timestamp: Date.now() / 1000,
                  metadata: { sources, thinking_steps: thinkingSteps, mode: mode as AIMode },
                }
                setMessages((prev) => [...prev, assistantMessage])
                setIsLoading(false)
                setStreamingMetadata({
                  currentMessage: '',
                  thinkingSteps: [],
                  sources: [],
                })
                eventSource.close()
                resolve()
                break
              }

              case 'error':
                eventSource.close()
                reject(new Error(eventData))
                break
            }
          } catch (err) {
            eventSource.close()
            reject(err)
          }
        }

        eventSource.onerror = () => {
          eventSource.close()
          reject(new Error('SSE connection error'))
        }
      })
    },
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

      try {
        let threadId: string
        let messageId: string

        const msgContext: Record<string, unknown> = { content_id: contentId }
        if (sourceOptions) {
          msgContext.source_options = sourceOptions
          if (sourceOptions.selected_content_ids?.length) {
            msgContext.content_ids = sourceOptions.selected_content_ids
          }
          msgContext.search_scope = sourceOptions.include_all_docs ? 'all' : 'selected'
        }

        if (!threadIdRef.current) {
          // 새 스레드 생성
          const resp = await httpClient.post<QueuedMessageResponse>('/threads', {
            query: content,
            mode: effectiveMode,
            reasoning,
            allow_remote: allowRemote,
            context: msgContext,
          })
          threadId = resp.thread_id
          messageId = resp.message_id
          threadIdRef.current = threadId
          setHasExistingThread(true)
        } else {
          // 기존 스레드에 메시지 추가
          threadId = threadIdRef.current
          const resp = await httpClient.post<QueuedMessageResponse>(
            `/threads/${threadId}/messages`,
            {
              query: content,
              mode: effectiveMode,
              reasoning,
              allow_remote: allowRemote,
              context: msgContext,
            }
          )
          messageId = resp.message_id
        }

        await connectEventSource(threadId, messageId, effectiveMode)
      } catch (err) {
        const error = err as Error
        setIsLoading(false)
        toast.error('메시지 전송 실패', { description: error.message })
      }
    },
    [contentId, connectEventSource, sourceOptions]
  )

  const regenerate = useCallback(
    async (_mode: string, reasoning: ReasoningPreference, allowRemote: boolean) => {
      if (!threadIdRef.current || messages.length === 0) return
      if (messages[messages.length - 1].role !== 'assistant') return

      // sourceOptions에서 mode 자동 결정
      const effectiveMode = sourceOptions?.include_web_search ? 'hybrid' : 'rag'

      setMessages((prev) => prev.slice(0, -1))
      setIsLoading(true)
      setStreamingMetadata({
        currentMessage: '',
        thinkingSteps: [],
        sources: [],
      })

      try {
        const stream = await httpClient.postStream(
          `/threads/${threadIdRef.current}/regenerate`,
          { mode: effectiveMode, reasoning, allow_remote: allowRemote }
        )
        await processSSEStream(stream, effectiveMode)
      } catch (err) {
        const error = err as Error
        setIsLoading(false)
        toast.error('재생성 실패', { description: error.message })
      }
    },
    [messages, processSSEStream, sourceOptions]
  )

  // 새 대화 시작: threadIdRef 초기화 + 메시지 클리어
  const startNewThread = useCallback(() => {
    threadIdRef.current = null
    setMessages([])
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
