/**
 * 채팅 스트리밍 서비스
 *
 * SSE 파싱 로직을 분리하여 재사용성과 테스트 용이성 확보
 * AbortController 지원으로 스트리밍 취소 가능
 */

import type { Message, Source, ThinkingStep } from '@/shared/types'
import { httpClient } from './api/httpClient'

// ============================================================
// Types
// ============================================================

export interface SSEChunk {
  type: 'accepted' | 'thread_id' | 'thinking' | 'thinking_step' | 'query_analysis' | 'source' | 'sources' | 'content' | 'partial_restore' | 'token' | 'done' | 'error' | 'search_queries'
  data: unknown
}

export interface AcceptedMessage {
  thread_id: string
  message_id: string
  user_message_id: string
}

export interface AgentMessageRequest {
  query: string
  mode: string
  model?: string
  context?: Record<string, unknown>
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
  onAccepted?: (message: AcceptedMessage) => void
  onThreadId?: (threadId: string) => void
}

export interface StreamResult {
  content: string
  sources: Source[]
  thinkingSteps: ThinkingStep[]
  accepted?: AcceptedMessage
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
  let accepted: AcceptedMessage | undefined
  let threadId: string | undefined

  try {
    while (true) {
      // 취소 요청 확인
      if (abortSignal?.aborted) {
        await reader.cancel()
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
          case 'accepted':
            accepted = chunk.data as AcceptedMessage
            callbacks.onAccepted?.(accepted)
            break

          case 'thread_id':
            threadId = chunk.data as string
            callbacks.onThreadId?.(threadId)
            break

          case 'thinking':
          case 'thinking_step':
          case 'query_analysis': {
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
            const doneData = chunk.data as {
              content?: string
              metadata?: Message['metadata']
            } | null
            if (typeof doneData?.content === 'string') {
              fullContent = doneData.content
            }
            const finalMessage: Message = {
              message_id: accepted?.message_id,
              role: 'assistant',
              content: fullContent,
              timestamp: Date.now() / 1000,
              metadata: {
                sources,
                thinking_steps: thinkingSteps,
                mode: mode,
                ...doneData?.metadata,
              },
            }
            callbacks.onComplete(finalMessage)
            return { content: fullContent, sources, thinkingSteps, accepted, threadId }
          }

          case 'error':
            throw new Error(chunk.data as string)
        }
      }
    }

    throw new Error('SSE stream ended before done')

  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // 취소된 경우 에러 콜백 호출하지 않음
      throw err
    }
    callbacks.onError(err as Error)
    throw err
  } finally {
    reader.releaseLock()
  }
}

// ============================================================
// API Functions
// ============================================================

async function postAgentStream(
  endpoint: string,
  body: Record<string, unknown>,
  mode: string,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  const stream = await httpClient.postStream(endpoint, body, { signal: abortSignal })
  await processSSEStream(new Response(stream), mode, callbacks, abortSignal)
}

export async function createThreadStream(
  request: AgentMessageRequest,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  await postAgentStream('/threads', { ...request, stream: true }, request.mode, callbacks, abortSignal)
}

export async function sendMessageStream(
  threadId: string,
  request: AgentMessageRequest,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  await postAgentStream(
    `/threads/${threadId}/messages`,
    { ...request, stream: true },
    request.mode,
    callbacks,
    abortSignal
  )
}

export async function regenerateStream(
  threadId: string,
  mode: string,
  model: string | undefined,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  await postAgentStream(
    `/threads/${threadId}/regenerate`,
    { mode, model },
    mode,
    callbacks,
    abortSignal
  )
}
