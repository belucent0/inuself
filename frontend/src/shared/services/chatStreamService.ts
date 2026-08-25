/**
 * 채팅 스트리밍 서비스
 *
 * SSE 파싱 로직을 분리하여 재사용성과 테스트 용이성 확보
 * AbortController 지원으로 스트리밍 취소 가능
 */

import type { Message, Source, ThinkingStep } from '@/shared/types'
import type { ReasoningPreference } from '@/shared/types'
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
  reasoning: ReasoningPreference
  allow_remote: boolean
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

interface RelayErrorData {
  code?: string
  message?: string
  error_id?: string
  retryable?: boolean
}

class RetryableStreamError extends Error {}
class TerminalStreamError extends Error {}

const RECONNECT_DELAYS_MS = [0, 1000, 2000, 5000]

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error))
}

function invokeCallback(callback: () => void): void {
  try {
    callback()
  } catch (error) {
    throw new TerminalStreamError(asError(error).message)
  }
}

function parseStreamError(data: unknown): Error {
  if (data && typeof data === 'object') {
    const relayError = data as RelayErrorData
    if (relayError.code === 'relay_unavailable' && relayError.retryable === true) {
      return new RetryableStreamError(relayError.message || 'Streaming relay unavailable')
    }
    if (typeof relayError.message === 'string') {
      return new TerminalStreamError(relayError.message)
    }
  }
  return new TerminalStreamError(typeof data === 'string' ? data : 'Agent stream failed')
}

function isRetryableConnectionError(error: unknown): boolean {
  if (error instanceof RetryableStreamError || error instanceof TypeError) return true
  const status = (error as { status?: unknown } | null)?.status
  return typeof status === 'number' && status >= 500
}

function abortError(): DOMException {
  return new DOMException('Aborted', 'AbortError')
}

async function waitForReconnect(baseDelayMs: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) throw abortError()
  if (baseDelayMs === 0) return

  const delayMs = Math.round(baseDelayMs * (0.8 + Math.random() * 0.4))
  await new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer)
      reject(abortError())
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, delayMs)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
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

      let result: ReadableStreamReadResult<Uint8Array>
      try {
        result = await reader.read()
      } catch (error) {
        if (isAbortError(error) || abortSignal?.aborted) throw abortError()
        throw new RetryableStreamError(asError(error).message)
      }
      if (abortSignal?.aborted) throw abortError()

      const { done, value } = result
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const chunk = parseSSELine(line)
        if (!chunk) continue

        switch (chunk.type) {
          case 'accepted': {
            const acceptedMessage = chunk.data as AcceptedMessage
            accepted = acceptedMessage
            const onAccepted = callbacks.onAccepted
            if (onAccepted) {
              invokeCallback(() => onAccepted(acceptedMessage))
            }
            break
          }

          case 'thread_id': {
            const streamedThreadId = chunk.data as string
            threadId = streamedThreadId
            const onThreadId = callbacks.onThreadId
            if (onThreadId) {
              invokeCallback(() => onThreadId(streamedThreadId))
            }
            break
          }

          case 'thinking':
          case 'thinking_step':
          case 'query_analysis': {
            const thinkingStep = chunk.data as ThinkingStep
            thinkingSteps.push(thinkingStep)
            invokeCallback(() => callbacks.onThinkingStep(thinkingStep))
            break
          }

          case 'source': {
            const source = chunk.data as Source
            sources.push(source)
            invokeCallback(() => callbacks.onSource(source))
            break
          }

          case 'sources':
            sources = Array.isArray(chunk.data) ? (chunk.data as Source[]) : []
            invokeCallback(() => callbacks.onSources(sources))
            break

          case 'search_queries':
            invokeCallback(() => callbacks.onSearchQueries(Array.isArray(chunk.data)
              ? (chunk.data as string[])
              : []))
            break

          case 'token': {
            const token = (chunk.data as string) || ''
            fullContent += token
            invokeCallback(() => callbacks.onToken(token))
            break
          }

          case 'content':
          case 'partial_restore': {
            const contentData = chunk.data as { content?: string } | string
            fullContent = typeof contentData === 'object' && contentData?.content
              ? contentData.content
              : (contentData as string) || ''
            const onContent = callbacks.onContent
            if (onContent) {
              invokeCallback(() => onContent(fullContent))
            }
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
            invokeCallback(() => callbacks.onComplete(finalMessage))
            return { content: fullContent, sources, thinkingSteps, accepted, threadId }
          }

          case 'error':
            throw parseStreamError(chunk.data)
        }
      }
    }

    throw new RetryableStreamError('SSE stream ended before done')

  } catch (err) {
    if (isAbortError(err)) {
      // 취소된 경우 에러 콜백 호출하지 않음
      throw err
    }
    callbacks.onError(err as Error)
    throw err
  } finally {
    try {
      await reader.cancel()
    } catch {
      // 이미 종료되었거나 네트워크에서 해제된 스트림
    }
    reader.releaseLock()
  }
}

// ============================================================
// API Functions
// ============================================================

async function postAgentStream(
  endpoint: string,
  body: unknown,
  mode: string,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  let accepted: AcceptedMessage | undefined
  const quietCallbacks: StreamingCallbacks = {
    ...callbacks,
    onAccepted: (message) => {
      accepted = message
      callbacks.onAccepted?.(message)
    },
    onError: () => {},
  }

  try {
    const stream = await httpClient.postStream(endpoint, body, { signal: abortSignal })
    await processSSEStream(new Response(stream), mode, quietCallbacks, abortSignal)
    return
  } catch (error) {
    if (isAbortError(error)) throw error
    if (!accepted || !isRetryableConnectionError(error)) {
      callbacks.onError(asError(error))
      throw error
    }
  }

  await resumeMessageStream(
    accepted.thread_id,
    accepted.message_id,
    mode,
    callbacks,
    abortSignal
  )
}

async function postAgentJob(
  endpoint: string,
  body: unknown,
  mode: string,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  let accepted: AcceptedMessage
  try {
    accepted = await httpClient.post<AcceptedMessage>(endpoint, body, { signal: abortSignal })
    const onAccepted = callbacks.onAccepted
    if (onAccepted) {
      invokeCallback(() => onAccepted(accepted))
    }
  } catch (error) {
    if (!isAbortError(error)) {
      callbacks.onError(asError(error))
    }
    throw error
  }

  await resumeMessageStream(
    accepted.thread_id,
    accepted.message_id,
    mode,
    callbacks,
    abortSignal
  )
}

export async function resumeMessageStream(
  threadId: string,
  messageId: string,
  mode: string,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  let lastError: Error = new RetryableStreamError('SSE connection lost')
  for (const delayMs of RECONNECT_DELAYS_MS) {
    await waitForReconnect(delayMs, abortSignal)
    try {
      const stream = await httpClient.getStream(
        `/threads/${threadId}/messages/${messageId}/stream`,
        { signal: abortSignal }
      )
      await processSSEStream(
        new Response(stream),
        mode,
        {
          ...callbacks,
          onError: () => {},
          onComplete: (message) => callbacks.onComplete({
            ...message,
            message_id: messageId,
          }),
        },
        abortSignal
      )
      return
    } catch (error) {
      if (isAbortError(error)) throw error
      if (!isRetryableConnectionError(error)) {
        callbacks.onError(asError(error))
        throw error
      }
      lastError = asError(error)
    }
  }

  callbacks.onError(lastError)
  throw lastError
}

export async function createThreadStream(
  request: AgentMessageRequest,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  await postAgentJob('/threads', request, request.mode, callbacks, abortSignal)
}

export async function sendMessageStream(
  threadId: string,
  request: AgentMessageRequest,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  await postAgentJob(
    `/threads/${threadId}/messages`,
    request,
    request.mode,
    callbacks,
    abortSignal
  )
}

export async function regenerateStream(
  threadId: string,
  mode: string,
  reasoning: ReasoningPreference,
  allowRemote: boolean,
  callbacks: StreamingCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  await postAgentStream(
    `/threads/${threadId}/regenerate`,
    { mode, reasoning, allow_remote: allowRemote },
    mode,
    callbacks,
    abortSignal
  )
}
