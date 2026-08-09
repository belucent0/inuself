/**
 * 채팅 스트리밍 서비스
 *
 * SSE 파싱 로직을 분리하여 재사용성과 테스트 용이성 확보
 * AbortController 지원으로 스트리밍 취소 가능
 */

import type { Message, Source, ThinkingStep } from '@/shared/types'
import { getAccessToken } from './authToken'

// ============================================================
// Types
// ============================================================

export interface SSEChunk {
  type: 'thread_id' | 'thinking' | 'thinking_step' | 'source' | 'sources' | 'content' | 'partial_restore' | 'token' | 'done' | 'error' | 'search_queries'
  data: unknown
}

export interface StreamingCallbacks {
  onToken: (token: string) => void
  onContent?: (content: string) => void
  onThinkingStep: (step: ThinkingStep) => void
  onSource: (source: Source) => void
  onSources: (sources: Source[]) => void
  onSearchQueries: (queries: string[]) => void
  onComplete: (message: Message) => void
  onError: (error: Error) => void
  onThreadId?: (threadId: string) => void
}

export interface StreamResult {
  content: string
  sources: Source[]
  thinkingSteps: ThinkingStep[]
  threadId?: string
}

// ============================================================
// SSE Parser
// ============================================================

export function parseSSELine(line: string): SSEChunk | null {
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

// ============================================================
// Stream Processor
// ============================================================

export async function processSSEStream(
  response: Response,
  mode: string,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<StreamResult> {
  if (!response.body) {
    throw new Error('No response body')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let fullContent = ''
  let sources: Source[] = []
  const thinkingSteps: ThinkingStep[] = []
  let threadId: string | undefined

  try {
    while (true) {
      // 취소 요청 확인
      if (abortSignal?.aborted) {
        reader.cancel()
        throw new DOMException('Aborted', 'AbortError')
      }

      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const chunk = parseSSELine(line)
        if (!chunk) continue

        switch (chunk.type) {
          case 'thread_id':
            threadId = chunk.data as string
            callbacks.onThreadId?.(threadId)
            break

          case 'thinking':
          case 'thinking_step': {
            const thinkingStep = chunk.data as ThinkingStep
            thinkingSteps.push(thinkingStep)
            callbacks.onThinkingStep(thinkingStep)
            break
          }

          case 'source': {
            const source = chunk.data as Source
            sources.push(source)
            callbacks.onSource(source)
            break
          }

          case 'sources':
            sources = Array.isArray(chunk.data) ? (chunk.data as Source[]) : []
            callbacks.onSources(sources)
            break

          case 'search_queries':
            callbacks.onSearchQueries(Array.isArray(chunk.data) ? (chunk.data as string[]) : [])
            break

          case 'token': {
            const token = (chunk.data as string) || ''
            fullContent += token
            callbacks.onToken(token)
            break
          }

          case 'content':
          case 'partial_restore': {
            const contentData = chunk.data as { content?: string } | string
            fullContent = typeof contentData === 'object' && contentData?.content
              ? contentData.content
              : (contentData as string) || ''
            callbacks.onContent?.(fullContent)
            break
          }

          case 'done': {
            const doneData = chunk.data as { content?: string } | null
            if (typeof doneData?.content === 'string') {
              fullContent = doneData.content
            }
            const finalMessage: Message = {
              role: 'assistant',
              content: fullContent,
              timestamp: Date.now() / 1000,
              metadata: {
                sources,
                thinking_steps: thinkingSteps,
                mode: mode,
              },
            }
            callbacks.onComplete(finalMessage)
            return { content: fullContent, sources, thinkingSteps, threadId }
          }

          case 'error':
            throw new Error(chunk.data as string)
        }
      }
    }

    // Stream ended without 'done' event
    const finalMessage: Message = {
      role: 'assistant',
      content: fullContent,
      timestamp: Date.now() / 1000,
      metadata: {
        sources,
        thinking_steps: thinkingSteps,
        mode: mode,
      },
    }
    callbacks.onComplete(finalMessage)
    return { content: fullContent, sources, thinkingSteps, threadId }

  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // 취소된 경우 에러 콜백 호출하지 않음
      throw err
    }
    callbacks.onError(err as Error)
    throw err
  }
}

// ============================================================
// Helper Functions
// ============================================================

/**
 * 인증 헤더를 포함한 기본 헤더를 생성합니다.
 */
function createAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const accessToken = getAccessToken()
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`
  }
  return headers
}

// ============================================================
// API Functions
// ============================================================

export async function regenerateStream(
  threadId: string,
  mode: string,
  model: string | undefined,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  const response = await fetch(`/api/threads/${threadId}/regenerate`, {
    method: 'POST',
    headers: createAuthHeaders(),
    body: JSON.stringify({ mode, model }),
    signal: abortSignal,
  })

  if (!response.ok) {
    throw new Error(`Failed to regenerate: ${response.statusText}`)
  }

  await processSSEStream(response, mode, callbacks, abortSignal)
}
