import assert from 'node:assert/strict'

import {
  processSSEStream,
  resumeMessageStream,
  sendMessageStream,
  type StreamingCallbacks,
} from '../src/shared/services/chatStreamService'
import { httpClient } from '../src/shared/services/api/httpClient'
import { tokenManager } from '../src/shared/services/tokenManager'
import { regenerateSummaryBlock } from '../src/shared/services/endpoints/contents'

const events = [
  { type: 'accepted', data: { thread_id: 'thread', message_id: 'answer', user_message_id: 'question' } },
  { type: 'query_analysis', data: { step: 'query_analysis', content: 'intent' } },
  { type: 'token', data: 'missed' },
  { type: 'partial_restore', data: 'restored' },
  { type: 'token', data: '!' },
  { type: 'done', data: { content: 'authoritative', metadata: { intent: { kind: 'search' } } } },
]
const response = new Response(
  events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('')
)

let displayedContent = ''
let completedContent = ''
let acceptedMessageId = ''
let queryAnalysis = ''
let completedMessageId = ''
let completedIntent = ''
const result = await processSSEStream(response, 'simple', {
  onToken: (token) => { displayedContent += token },
  onContent: (content) => { displayedContent = content },
  onThinkingStep: (step) => { queryAnalysis = step.content },
  onSource: () => {},
  onSources: () => {},
  onSearchQueries: () => {},
  onComplete: (message) => {
    completedContent = message.content
    completedMessageId = message.message_id || ''
    completedIntent = (message.metadata?.intent as { kind?: string })?.kind || ''
  },
  onError: (error) => { throw error },
  onAccepted: (message) => { acceptedMessageId = message.message_id },
})

assert.equal(displayedContent, 'restored!')
assert.equal(completedContent, 'authoritative')
assert.equal(result.content, 'authoritative')
assert.equal(acceptedMessageId, 'answer')
assert.equal(result.accepted?.thread_id, 'thread')
assert.equal(queryAnalysis, 'intent')
assert.equal(completedMessageId, 'answer')
assert.equal(completedIntent, 'search')

let streamError = ''
await assert.rejects(
  processSSEStream(
    new Response(`data: ${JSON.stringify({ type: 'token', data: 'partial' })}\n\n`),
    'simple',
    {
      onToken: () => {},
      onThinkingStep: () => {},
      onSource: () => {},
      onSources: () => {},
      onSearchQueries: () => {},
      onComplete: () => { throw new Error('truncated stream completed') },
      onError: (error) => { streamError = error.message },
    }
  ),
  /ended before done/
)
assert.equal(streamError, 'SSE stream ended before done')

await assert.rejects(
  processSSEStream(
    new Response(`data: ${JSON.stringify({ type: 'error', data: { message: 'safe error', error_id: 'err_1' } })}\n\n`),
    'simple',
    { onToken: () => {}, onThinkingStep: () => {}, onSource: () => {}, onSources: () => {}, onSearchQueries: () => {}, onComplete: () => {}, onError: () => {} }
  ),
  /safe error/
)

const storageValues = new Map<string, string>([
  ['auth_refresh_token', 'refresh-token'],
])
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: {
    getItem: (key: string) => storageValues.get(key) ?? null,
    setItem: (key: string, value: string) => storageValues.set(key, value),
    removeItem: (key: string) => storageValues.delete(key),
    clear: () => storageValues.clear(),
    key: (index: number) => [...storageValues.keys()][index] ?? null,
    get length() { return storageValues.size },
  } as Storage,
})

const callbackState = {
  accepted: 0,
  completed: 0,
  errors: 0,
  content: '',
  messageId: '',
}
const reconnectCallbacks: StreamingCallbacks = {
  onAccepted: () => { callbackState.accepted += 1 },
  onToken: (token) => { callbackState.content += token },
  onContent: (content) => { callbackState.content = content },
  onThinkingStep: () => {},
  onSource: () => {},
  onSources: () => {},
  onSearchQueries: () => {},
  onComplete: (message) => {
    callbackState.completed += 1
    callbackState.content = message.content
    callbackState.messageId = message.message_id || ''
  },
  onError: () => { callbackState.errors += 1 },
}

const originalFetch = globalThis.fetch
const reconnectRequests: string[] = []
globalThis.fetch = async (input, init) => {
  reconnectRequests.push(`${init?.method || 'GET'} ${String(input)}`)
  if (init?.method === 'POST') {
    return new Response(
      `data: ${JSON.stringify({ type: 'accepted', data: { thread_id: 'thread', message_id: 'answer', user_message_id: 'question' } })}\n\n` +
      `data: ${JSON.stringify({ type: 'error', data: { code: 'relay_unavailable', message: 'retry', retryable: true } })}\n\n`
    )
  }
  return new Response(
    `data: ${JSON.stringify({ type: 'partial_restore', data: 'recovered' })}\n\n` +
    `data: ${JSON.stringify({ type: 'done', data: { content: 'final' } })}\n\n`
  )
}

await sendMessageStream(
  'thread',
  { query: 'hello', mode: 'simple' },
  reconnectCallbacks
)

assert.deepEqual(reconnectRequests, [
  'POST /api/threads/thread/messages',
  'GET /api/threads/thread/messages/answer/stream',
])
assert.equal(callbackState.accepted, 1)
assert.equal(callbackState.completed, 1)
assert.equal(callbackState.errors, 0)
assert.equal(callbackState.content, 'final')
assert.equal(callbackState.messageId, 'answer')

let eofFetches = 0
let eofCompleted = 0
globalThis.fetch = async (_input, init) => {
  eofFetches += 1
  if (init?.method === 'POST') {
    return new Response(
      `data: ${JSON.stringify({ type: 'accepted', data: { thread_id: 'thread', message_id: 'eof', user_message_id: 'question' } })}\n\n`
    )
  }
  return new Response(
    `data: ${JSON.stringify({ type: 'done', data: { content: 'recovered after eof' } })}\n\n`
  )
}

await sendMessageStream(
  'thread',
  { query: 'eof', mode: 'simple' },
  {
    ...reconnectCallbacks,
    onComplete: () => { eofCompleted += 1 },
  }
)
assert.equal(eofFetches, 2)
assert.equal(eofCompleted, 1)

let terminalErrorCallbacks = 0
let terminalFetches = 0
globalThis.fetch = async () => {
  terminalFetches += 1
  return new Response(
    `data: ${JSON.stringify({ type: 'accepted', data: { thread_id: 'thread', message_id: 'failed', user_message_id: 'question' } })}\n\n` +
    `data: ${JSON.stringify({ type: 'error', data: 'worker failed' })}\n\n`
  )
}

await assert.rejects(
  sendMessageStream(
    'thread',
    { query: 'fail', mode: 'simple' },
    {
      ...reconnectCallbacks,
      onError: () => { terminalErrorCallbacks += 1 },
    }
  ),
  /worker failed/
)
assert.equal(terminalFetches, 1)
assert.equal(terminalErrorCallbacks, 1)

let callbackErrorFetches = 0
globalThis.fetch = async () => {
  callbackErrorFetches += 1
  return new Response(
    `data: ${JSON.stringify({ type: 'accepted', data: { thread_id: 'thread', message_id: 'callback-error', user_message_id: 'question' } })}\n\n` +
    `data: ${JSON.stringify({ type: 'done', data: { content: 'done' } })}\n\n`
  )
}

await assert.rejects(
  sendMessageStream(
    'thread',
    { query: 'callback error', mode: 'simple' },
    {
      ...reconnectCallbacks,
      onComplete: () => { throw new TypeError('UI callback failed') },
    }
  ),
  /UI callback failed/
)
assert.equal(callbackErrorFetches, 1)

let unacceptedFetches = 0
let unacceptedErrors = 0
globalThis.fetch = async () => {
  unacceptedFetches += 1
  return new Response(
    `data: ${JSON.stringify({ type: 'token', data: 'not accepted' })}\n\n`
  )
}

await assert.rejects(
  sendMessageStream(
    'thread',
    { query: 'ambiguous', mode: 'simple' },
    {
      ...reconnectCallbacks,
      onError: () => { unacceptedErrors += 1 },
    }
  ),
  /ended before done/
)
assert.equal(unacceptedFetches, 1)
assert.equal(unacceptedErrors, 1)

const abortController = new AbortController()
let abortFetches = 0
let abortErrors = 0
globalThis.fetch = async (_input, init) => {
  abortFetches += 1
  if (init?.method === 'POST') {
    return new Response(
      `data: ${JSON.stringify({ type: 'accepted', data: { thread_id: 'thread', message_id: 'abort', user_message_id: 'question' } })}\n\n` +
      `data: ${JSON.stringify({ type: 'error', data: { code: 'relay_unavailable', message: 'retry', retryable: true } })}\n\n`
    )
  }
  setTimeout(() => abortController.abort(), 10)
  return new Response('', { status: 503, statusText: 'Unavailable' })
}

const abortStartedAt = Date.now()
await assert.rejects(
  sendMessageStream(
    'thread',
    { query: 'abort retry', mode: 'simple' },
    {
      ...reconnectCallbacks,
      onError: () => { abortErrors += 1 },
    },
    abortController.signal
  ),
  (error: unknown) => error instanceof DOMException && error.name === 'AbortError'
)
assert.equal(abortFetches, 2)
assert.equal(abortErrors, 0)
assert.ok(Date.now() - abortStartedAt < 500)

const originalSetTimeout = globalThis.setTimeout
globalThis.setTimeout = ((handler: TimerHandler, _timeout?: number, ...args: unknown[]) => {
  queueMicrotask(() => {
    if (typeof handler === 'function') handler(...args)
  })
  return 0
}) as typeof setTimeout

let exhaustedFetches = 0
let exhaustedErrors = 0
globalThis.fetch = async (_input, init) => {
  exhaustedFetches += 1
  if (init?.method === 'POST') {
    return new Response(
      `data: ${JSON.stringify({ type: 'accepted', data: { thread_id: 'thread', message_id: 'exhausted', user_message_id: 'question' } })}\n\n` +
      `data: ${JSON.stringify({ type: 'error', data: { code: 'relay_unavailable', message: 'retry', retryable: true } })}\n\n`
    )
  }
  return new Response('', { status: 503, statusText: 'Unavailable' })
}

await assert.rejects(
  sendMessageStream(
    'thread',
    { query: 'exhaust retries', mode: 'simple' },
    {
      ...reconnectCallbacks,
      onError: () => { exhaustedErrors += 1 },
    }
  ),
  /503/
)
assert.equal(exhaustedFetches, 5)
assert.equal(exhaustedErrors, 1)
globalThis.setTimeout = originalSetTimeout

storageValues.set('auth_access_token', 'resume-access')
const resumeRequests: string[] = []
const resumeHeaders: Array<string | null> = []
let resumeAttempts = 0
globalThis.setTimeout = ((handler: TimerHandler, _timeout?: number, ...args: unknown[]) => {
  queueMicrotask(() => { if (typeof handler === 'function') handler(...args) })
  return 0
}) as typeof setTimeout
globalThis.fetch = async (input, init) => {
  resumeAttempts += 1
  resumeRequests.push(String(input))
  resumeHeaders.push(new Headers(init?.headers).get('Authorization'))
  if (resumeAttempts === 1) throw new TypeError('network lost')
  return new Response(`data: ${JSON.stringify({ type: 'partial_restore', data: 'restored' })}\n\n` + `data: ${JSON.stringify({ type: 'done', data: { content: 'final' } })}\n\n`)
}
let resumedMessageId = ''
await resumeMessageStream('thread', 'message', 'simple', {
  ...reconnectCallbacks,
  onComplete: (message) => { resumedMessageId = message.message_id || '' },
})
globalThis.setTimeout = originalSetTimeout
assert.deepEqual(resumeRequests, ['/api/threads/thread/messages/message/stream', '/api/threads/thread/messages/message/stream'])
assert.deepEqual(resumeHeaders, ['Bearer resume-access', 'Bearer resume-access'])
assert.equal(resumedMessageId, 'message')
storageValues.delete('auth_access_token')

const authRequests: string[] = []
const authHeaders: Array<string | null> = []
let protectedGetCount = 0
globalThis.fetch = async (input, init) => {
  const url = String(input)
  authRequests.push(`${init?.method || 'GET'} ${url}`)
  if (url.endsWith('/auth/refresh')) {
    return new Response(JSON.stringify({
      access_token: 'refreshed-access',
      refresh_token: 'refreshed-refresh',
      access_expires_in: 3600,
    }), { status: 200 })
  }

  protectedGetCount += 1
  authHeaders.push(new Headers(init?.headers).get('Authorization'))
  if (protectedGetCount === 1) {
    return new Response('expired', { status: 401, statusText: 'Unauthorized' })
  }
  return new Response('authenticated stream')
}

try {
  const authStream = await httpClient.getStream('/retry-auth')
  assert.equal(await new Response(authStream).text(), 'authenticated stream')
} finally {
  tokenManager.stop()
}
assert.deepEqual(authRequests, [
  'GET /api/retry-auth',
  'POST /api/auth/refresh',
  'GET /api/retry-auth',
])
assert.deepEqual(authHeaders, [null, 'Bearer refreshed-access'])

globalThis.fetch = async () => new Response(JSON.stringify({
  success: false,
  block_key: 'title',
  message: 'generation failed',
}), { status: 200, headers: { 'Content-Type': 'application/json' } })
await assert.rejects(
  regenerateSummaryBlock('content', 'title'),
  /generation failed/
)

globalThis.fetch = originalFetch
