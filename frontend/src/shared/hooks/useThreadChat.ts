/**
 * Vercel AI SDK 스타일의 스레드 채팅 훅
 *
 * 기존 SSE 로직을 유지하면서 AI SDK의 API 패턴을 차용:
 * - 선언적 API (messages, input, handleSubmit)
 * - Optimistic updates
 * - 자동 상태 관리
 * - 에러 핸들링
 *
 * 향후 백엔드를 AI SDK StreamData 프로토콜로 변경하면
 * @ai-sdk/react의 useChat으로 완전히 교체 가능
 */

import { useState, useCallback, useRef, useEffect } from 'react'
import { toast } from 'sonner'
import { httpClient } from '@/shared/services'
import type { Message, Source, ThinkingStep } from '@/shared/types'

interface ThreadChatOptions {
  threadId: string
  initialMessages?: Message[]
  onMessageComplete?: (message: Message) => void
}

interface ChatState {
  messages: Message[]
  isLoading: boolean
  error: Error | null
}

interface StreamingMetadata {
  currentMessage: string
  thinkingSteps: ThinkingStep[]
  sources: Source[]
  searchQueries: string[]
}

interface SSEChunk {
  type: 'thinking' | 'source' | 'sources' | 'content' | 'token' | 'done' | 'error' | 'search_queries'
  data: unknown
}

export function useThreadChat({ threadId, initialMessages = [], onMessageComplete }: ThreadChatOptions) {
  // 메시지 상태 (AI SDK 패턴)
  const [state, setState] = useState<ChatState>({
    messages: initialMessages,
    isLoading: false,
    error: null,
  })

  // 입력 상태 (AI SDK 패턴)
  const [input, setInput] = useState('')

  // 스트리밍 메타데이터
  const [streamingMetadata, setStreamingMetadata] = useState<StreamingMetadata>({
    currentMessage: '',
    thinkingSteps: [],
    sources: [],
    searchQueries: [],
  })

  // 초기 메시지 동기화 - 스트리밍 중이 아닐 때만
  const initialMessagesRef = useRef<Message[]>(initialMessages)
  useEffect(() => {
    // 스트리밍 중이면 초기 메시지로 덮어쓰지 않음
    if (state.isLoading) return

    // 초기 메시지가 변경되었고, 현재 메시지보다 많을 때만 동기화
    // (서버에서 새 메시지를 가져왔을 때)
    if (initialMessages.length > 0 && initialMessages.length >= state.messages.length) {
      // 내용이 실제로 다른지 확인
      const currentIds = state.messages.map(m => `${m.role}-${m.content.slice(0, 50)}`).join('|')
      const newIds = initialMessages.map(m => `${m.role}-${m.content.slice(0, 50)}`).join('|')

      if (currentIds !== newIds) {
        setState((prev) => ({ ...prev, messages: initialMessages }))
      }
    }
    initialMessagesRef.current = initialMessages
  }, [initialMessages, state.isLoading, state.messages.length])

  // SSE 파싱 로직 (기존 유지)
  const parseSSEChunk = (line: string): SSEChunk | null => {
    if (!line.startsWith('data: ')) return null

    const dataStr = line.slice(6).trim()
    if (dataStr === '[DONE]') {
      return { type: 'done', data: null }
    }

    try {
      return JSON.parse(dataStr) as SSEChunk
    } catch {
      return null
    }
  }

  // SSE 스트림 처리 공통 로직
  const processSSEStream = useCallback(
    async (
      stream: ReadableStream<Uint8Array>,
      mode: string
    ) => {
      const reader = stream.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullContent = ''
      let sources: Source[] = []
      let thinkingSteps: ThinkingStep[] = []
      let searchQueries: string[] = []

      // SSE 스트리밍 처리
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
              sources.push(chunk.data as Source)
              setStreamingMetadata((prev) => ({
                ...prev,
                sources: [...sources],
              }))
              break

            case 'sources':
              sources = Array.isArray(chunk.data) ? (chunk.data as Source[]) : []
              setStreamingMetadata((prev) => ({
                ...prev,
                sources: [...sources],
              }))
              break

            case 'search_queries':
              searchQueries = Array.isArray(chunk.data) ? (chunk.data as string[]) : []
              setStreamingMetadata((prev) => ({
                ...prev,
                searchQueries: [...searchQueries],
              }))
              break

            case 'token':
              const token = (chunk.data as string) || ''
              fullContent += token
              setStreamingMetadata((prev) => ({
                ...prev,
                currentMessage: fullContent,
              }))
              break

            case 'content':
              const contentData = chunk.data as { content?: string } | string
              fullContent = typeof contentData === 'object' && contentData?.content
                ? contentData.content
                : (contentData as string) || ''
              setStreamingMetadata((prev) => ({
                ...prev,
                currentMessage: fullContent,
              }))
              break

            case 'done':
              // 완료된 메시지 추가
              const assistantMessage: Message = {
                role: 'assistant',
                content: fullContent,
                timestamp: Date.now() / 1000,
                metadata: {
                  sources,
                  thinking_steps: thinkingSteps,
                  mode,
                },
              }

              setState((prev) => ({
                ...prev,
                messages: [...prev.messages, assistantMessage],
                isLoading: false,
              }))

              setStreamingMetadata({
                currentMessage: '',
                thinkingSteps: [],
                sources: [],
                searchQueries: [],
              })

              if (onMessageComplete) {
                onMessageComplete(assistantMessage)
              }
              return

            case 'error':
              throw new Error(chunk.data as string)
          }
        }
      }
    },
    [onMessageComplete]
  )

  // 메시지 전송 (AI SDK 패턴의 append)
  const append = useCallback(
    async (message: { role: 'user'; content: string }, options?: { body?: { mode?: string; context?: unknown } }) => {
      const mode = options?.body?.mode || 'auto'

      // Optimistic update: 사용자 메시지 즉시 추가
      const userMessage: Message = {
        role: 'user',
        content: message.content,
        timestamp: Date.now() / 1000,
      }

      setState((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
        isLoading: true,
        error: null,
      }))

      // 스트리밍 메타데이터 초기화
      setStreamingMetadata({
        currentMessage: '',
        thinkingSteps: [],
        sources: [],
        searchQueries: [],
      })

      try {
        const stream = await httpClient.postStream(`/threads/${threadId}/messages/stream`, {
          query: message.content,
          mode,
          context: options?.body?.context,
        })

        await processSSEStream(stream, mode)
      } catch (err) {
        const error = err as Error
        setState((prev) => ({
          ...prev,
          isLoading: false,
          error,
        }))
        toast.error('메시지를 전송하지 못했습니다', {
          description: error.message,
        })
      }
    },
    [threadId, processSSEStream]
  )

  // 폼 제출 핸들러 (AI SDK 패턴)
  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      if (!input.trim() || state.isLoading) return

      const userInput = input
      setInput('') // 입력 즉시 클리어

      await append({ role: 'user', content: userInput })
    },
    [input, state.isLoading, append]
  )

  // 입력 변경 핸들러 (AI SDK 패턴)
  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    setInput(e.target.value)
  }, [])

  // 커스텀 전송 함수 (모드 지정 가능)
  const sendMessage = useCallback(
    async (content: string, mode?: string) => {
      await append({ role: 'user', content }, { body: { mode } })
    },
    [append]
  )

  // AI 응답만 요청 (사용자 메시지 추가 없이)
  // 랜딩 페이지에서 이미 사용자 메시지가 저장된 경우 사용
  const requestAIResponse = useCallback(
    async (userQuery: string, mode?: string) => {
      // 사용자 메시지를 추가하지 않고 AI 응답만 요청
      setState((prev) => ({
        ...prev,
        isLoading: true,
        error: null,
      }))

      setStreamingMetadata({
        currentMessage: '',
        thinkingSteps: [],
        sources: [],
        searchQueries: [],
      })

      try {
        const stream = await httpClient.postStream(`/threads/${threadId}/messages/stream`, {
          query: userQuery,
          mode: mode || 'auto',
        })

        await processSSEStream(stream, mode || 'auto')
      } catch (err) {
        const error = err as Error
        setState((prev) => ({
          ...prev,
          isLoading: false,
          error,
        }))
        toast.error('AI 응답을 받지 못했습니다', {
          description: error.message,
        })
      }
    },
    [threadId, processSSEStream]
  )

  // 답변 재생성
  const regenerate = useCallback(
    async (mode?: string) => {
      // 마지막 메시지가 assistant인지 확인
      if (state.messages.length === 0 || state.messages[state.messages.length - 1].role !== 'assistant') {
        toast.error('재생성할 답변이 없습니다')
        return
      }

      // 로컬에서 마지막 assistant 메시지 제거
      setState((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -1),
        isLoading: true,
        error: null,
      }))

      setStreamingMetadata({
        currentMessage: '',
        thinkingSteps: [],
        sources: [],
        searchQueries: [],
      })

      try {
        const stream = await httpClient.postStream(`/threads/${threadId}/regenerate`, {
          mode: mode || 'auto',
        })

        await processSSEStream(stream, mode || 'auto')
      } catch (err) {
        const error = err as Error
        setState((prev) => ({
          ...prev,
          isLoading: false,
          error,
        }))
        toast.error('답변 재생성에 실패했습니다', {
          description: error.message,
        })
      }
    },
    [threadId, state.messages, processSSEStream]
  )

  return {
    // AI SDK 호환 API
    messages: state.messages,
    input,
    handleInputChange,
    handleSubmit,
    isLoading: state.isLoading,
    error: state.error,

    // 커스텀 API
    sendMessage,
    requestAIResponse,
    regenerate,  // 답변 재생성
    isStreaming: state.isLoading,
    currentStreamingMessage: streamingMetadata.currentMessage,
    currentThinkingSteps: streamingMetadata.thinkingSteps,
    currentSources: streamingMetadata.sources,
    currentSearchQueries: streamingMetadata.searchQueries,

    // 메시지 직접 설정 (초기화용)
    setMessages: (messages: Message[]) => {
      setState((prev) => ({ ...prev, messages }))
    },
  }
}
