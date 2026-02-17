/**
 * 콘텐츠용 채팅 훅
 * - 첫 메시지: 스레드 생성 + 메시지 전송 (SSE)
 * - 이후 메시지: 기존 스레드에 메시지 추가
 * - thread_created SSE 이벤트로 스레드 ID 수신
 */

import { useState, useCallback, useRef } from 'react'
import { toast } from 'sonner'
import { httpClient } from '@/shared/services'
import type { SearchSource, ThinkingStep, AIMode } from '@/features/chat/types'

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

export function useContentChat(contentId: string, _contentTitle: string) {
  const [messages, setMessages] = useState<ContentMessage[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const threadIdRef = useRef<string | null>(null)

  const [streamingMetadata, setStreamingMetadata] = useState<StreamingMetadata>({
    currentMessage: '',
    thinkingSteps: [],
    sources: [],
  })

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
            case 'thread_created': {
              const data = chunk.data as { thread_id: string }
              threadIdRef.current = data.thread_id
              break
            }
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

  const sendMessage = useCallback(
    async (content: string, mode?: string, model?: string) => {
      const effectiveMode = mode || 'hybrid'

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
        let stream: ReadableStream<Uint8Array>

        if (!threadIdRef.current) {
          stream = await httpClient.postStream('/threads/stream', {
            query: content,
            mode: effectiveMode,
            context: { content_id: contentId },
            model,
          })
        } else {
          stream = await httpClient.postStream(
            `/threads/${threadIdRef.current}/messages/stream`,
            {
              query: content,
              mode: effectiveMode,
              context: { content_id: contentId },
              model,
            }
          )
        }

        await processSSEStream(stream, effectiveMode)
      } catch (err) {
        const error = err as Error
        setIsLoading(false)
        toast.error('메시지 전송 실패', { description: error.message })
      }
    },
    [contentId, processSSEStream]
  )

  const regenerate = useCallback(
    async (mode?: string, model?: string) => {
      if (!threadIdRef.current || messages.length === 0) return
      if (messages[messages.length - 1].role !== 'assistant') return

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
          { mode: mode || 'hybrid', model }
        )
        await processSSEStream(stream, mode || 'hybrid')
      } catch (err) {
        const error = err as Error
        setIsLoading(false)
        toast.error('재생성 실패', { description: error.message })
      }
    },
    [messages, processSSEStream]
  )

  return {
    messages,
    isStreaming: isLoading,
    currentStreamingMessage: streamingMetadata.currentMessage,
    currentThinkingSteps: streamingMetadata.thinkingSteps,
    currentSources: streamingMetadata.sources,
    sendMessage,
    regenerate,
  }
}
