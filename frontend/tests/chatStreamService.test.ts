import assert from 'node:assert/strict'

import {
  createThreadStream,
  processSSEStream,
  regenerateStream,
  resumeMessageStream,
  sendMessageStream,
  type StreamingCallbacks,
} from '../src/shared/services/chatStreamService'
import { regenerateSummaryBlock } from '../src/shared/services/endpoints/contents'
import { httpClient } from '../src/shared/services/api/httpClient'
import { useChatStore } from '../src/shared/stores/chatStore'
import { enterAcceptedHomeThread } from '../src/pages/homeThreadTransition'

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
    {
      onToken: () => {},
      onThinkingStep: () => {},
      onSource: () => {},
      onSources: () => {},
      onSearchQueries: () => {},
      onComplete: () => {},
      onError: () => {},
    }
  ),
  /safe error/
)

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
const homeRequests: string[] = []
globalThis.fetch = async (input, init) => {
  homeRequests.push(`${init?.method || 'GET'} ${String(input)}`)
  if (init?.method === 'POST') {
    return Response.json({
      thread_id: 'home-thread',
      message_id: 'home-assistant',
      user_message_id: 'home-user',
    })
  }
  return new Response(
    `data: ${JSON.stringify({ type: 'done', data: { content: 'home answer' } })}\n\n`
  )
}
const homeAccepted = await httpClient.post<{
  thread_id: string
  message_id: string
  user_message_id: string
}>('/threads', { query: 'home question', mode: 'simple' })
let homeNavigation = ''
enterAcceptedHomeThread(homeAccepted, 'home question', 'simple', (to) => {
  homeNavigation = to
})
useChatStore.getState().startStreamingMode()
await resumeMessageStream(
  homeAccepted.thread_id,
  homeAccepted.message_id,
  'simple',
  {
    ...reconnectCallbacks,
    onComplete: (message) => {
      useChatStore.getState().finishStreaming(
        message.content,
        message.metadata,
        homeAccepted.message_id
      )
    },
  }
)
assert.deepEqual(homeRequests, [
  'POST /api/threads',
  'GET /api/threads/home-thread/messages/home-assistant/stream',
])
assert.equal(homeNavigation, '/chat/home-thread?messageId=home-assistant')
assert.deepEqual(
  useChatStore.getState().messages.map(({ message_id, role, content }) => ({
    message_id,
    role,
    content,
  })),
  [
    { message_id: 'home-user', role: 'user', content: 'home question' },
    { message_id: 'home-assistant', role: 'assistant', content: 'home answer' },
  ]
)

const reconnectRequests: string[] = []
let sendBody: unknown
const reconnectCredentials: Array<RequestCredentials | undefined> = []
const reconnectAuthorization: Array<string | null> = []
globalThis.fetch = async (input, init) => {
  reconnectRequests.push(`${init?.method || 'GET'} ${String(input)}`)
  reconnectCredentials.push(init?.credentials)
  reconnectAuthorization.push(new Headers(init?.headers).get('Authorization'))
  if (init?.method === 'POST') {
    sendBody = JSON.parse(String(init?.body))
    return Response.json({ thread_id: 'thread', message_id: 'answer', user_message_id: 'question' })
  }
  return new Response(
    `data: ${JSON.stringify({ type: 'partial_restore', data: 'recovered' })}\n\n` +
    `data: ${JSON.stringify({ type: 'done', data: { content: 'final' } })}\n\n`
  )
}

await sendMessageStream(
  'thread',
  { query: 'hello', mode: 'simple', reasoning: 'none', allow_remote: false },
  reconnectCallbacks
)

assert.deepEqual(reconnectRequests, [
  'POST /api/threads/thread/messages',
  'GET /api/threads/thread/messages/answer/stream',
])
assert.deepEqual(sendBody, {
  query: 'hello',
  mode: 'simple',
  reasoning: 'none',
  allow_remote: false,
})
assert.deepEqual(reconnectCredentials, ['same-origin', 'same-origin'])
assert.deepEqual(reconnectAuthorization, [null, null])
assert.equal(callbackState.accepted, 1)
assert.equal(callbackState.completed, 1)
assert.equal(callbackState.errors, 0)
assert.equal(callbackState.content, 'final')
assert.equal(callbackState.messageId, 'answer')

let createBody: unknown
const createRequests: string[] = []
globalThis.fetch = async (input, init) => {
  createRequests.push(`${init?.method || 'GET'} ${String(input)}`)
  if (init?.method === 'POST') {
    createBody = JSON.parse(String(init?.body))
    return Response.json({ thread_id: 'created', message_id: 'answer', user_message_id: 'question' })
  }
  return new Response(`data: ${JSON.stringify({ type: 'done', data: { content: 'created' } })}\n\n`)
}
await createThreadStream(
  { query: 'new', mode: 'simple', reasoning: 'low', allow_remote: false },
  reconnectCallbacks
)
assert.deepEqual(createBody, {
  query: 'new',
  mode: 'simple',
  reasoning: 'low',
  allow_remote: false,
})
assert.deepEqual(createRequests, [
  'POST /api/threads',
  'GET /api/threads/created/messages/answer/stream',
])

let eofFetches = 0
let eofCompleted = 0
globalThis.fetch = async (_input, init) => {
  eofFetches += 1
  if (init?.method === 'POST') {
    return Response.json({ thread_id: 'thread', message_id: 'eof', user_message_id: 'question' })
  }
  if (eofFetches === 2) {
    return new Response(`data: ${JSON.stringify({ type: 'token', data: 'partial' })}\n\n`)
  }
  return new Response(
    `data: ${JSON.stringify({ type: 'done', data: { content: 'recovered after eof' } })}\n\n`
  )
}

await sendMessageStream(
  'thread',
  { query: 'eof', mode: 'simple', reasoning: 'none', allow_remote: false },
  {
    ...reconnectCallbacks,
    onComplete: () => { eofCompleted += 1 },
  }
)
assert.equal(eofFetches, 3)
assert.equal(eofCompleted, 1)

let terminalErrorCallbacks = 0
let terminalFetches = 0
globalThis.fetch = async (_input, init) => {
  terminalFetches += 1
  if (init?.method === 'POST') {
    return Response.json({ thread_id: 'thread', message_id: 'failed', user_message_id: 'question' })
  }
  return new Response(
    `data: ${JSON.stringify({ type: 'error', data: 'worker failed' })}\n\n`
  )
}

await assert.rejects(
  sendMessageStream(
    'thread',
    { query: 'fail', mode: 'simple', reasoning: 'none', allow_remote: false },
    {
      ...reconnectCallbacks,
      onError: () => { terminalErrorCallbacks += 1 },
    }
  ),
  /worker failed/
)
assert.equal(terminalFetches, 2)
assert.equal(terminalErrorCallbacks, 1)

let callbackErrorFetches = 0
globalThis.fetch = async (_input, init) => {
  callbackErrorFetches += 1
  if (init?.method === 'POST') {
    return Response.json({ thread_id: 'thread', message_id: 'callback-error', user_message_id: 'question' })
  }
  return new Response(
    `data: ${JSON.stringify({ type: 'done', data: { content: 'done' } })}\n\n`
  )
}

await assert.rejects(
  sendMessageStream(
    'thread',
    { query: 'callback error', mode: 'simple', reasoning: 'none', allow_remote: false },
    {
      ...reconnectCallbacks,
      onComplete: () => { throw new TypeError('UI callback failed') },
    }
  ),
  /UI callback failed/
)
assert.equal(callbackErrorFetches, 2)

let unacceptedFetches = 0
let unacceptedErrors = 0
globalThis.fetch = async () => {
  unacceptedFetches += 1
  return new Response('', { status: 500, statusText: 'Unavailable' })
}

await assert.rejects(
  sendMessageStream(
    'thread',
    { query: 'ambiguous', mode: 'simple', reasoning: 'none', allow_remote: false },
    {
      ...reconnectCallbacks,
      onError: () => { unacceptedErrors += 1 },
    }
  ),
  /500/
)
assert.equal(unacceptedFetches, 1)
assert.equal(unacceptedErrors, 1)

const abortController = new AbortController()
let abortFetches = 0
let abortErrors = 0
globalThis.fetch = async (_input, init) => {
  abortFetches += 1
  if (init?.method === 'POST') {
    return Response.json({ thread_id: 'thread', message_id: 'abort', user_message_id: 'question' })
  }
  setTimeout(() => abortController.abort(), 10)
  return new Response('', { status: 503, statusText: 'Unavailable' })
}

const abortStartedAt = Date.now()
await assert.rejects(
  sendMessageStream(
    'thread',
    { query: 'abort retry', mode: 'simple', reasoning: 'none', allow_remote: false },
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
    return Response.json({ thread_id: 'thread', message_id: 'exhausted', user_message_id: 'question' })
  }
  return new Response('', { status: 503, statusText: 'Unavailable' })
}

await assert.rejects(
  sendMessageStream(
    'thread',
    { query: 'exhaust retries', mode: 'simple', reasoning: 'none', allow_remote: false },
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

const resumeRequests: string[] = []
const resumeCredentials: Array<RequestCredentials | undefined> = []
const resumeAuthorization: Array<string | null> = []
let resumeAttempts = 0
globalThis.setTimeout = ((handler: TimerHandler, _timeout?: number, ...args: unknown[]) => {
  queueMicrotask(() => { if (typeof handler === 'function') handler(...args) })
  return 0
}) as typeof setTimeout
globalThis.fetch = async (input, init) => {
  resumeAttempts += 1
  resumeRequests.push(String(input))
  resumeCredentials.push(init?.credentials)
  resumeAuthorization.push(new Headers(init?.headers).get('Authorization'))
  if (resumeAttempts === 1) throw new TypeError('network lost')
  return new Response(
    `data: ${JSON.stringify({ type: 'partial_restore', data: 'restored' })}\n\n` +
    `data: ${JSON.stringify({ type: 'done', data: { content: 'final' } })}\n\n`
  )
}
let resumedMessageId = ''
await resumeMessageStream('thread', 'message', 'simple', {
  ...reconnectCallbacks,
  onComplete: (message) => { resumedMessageId = message.message_id || '' },
})
globalThis.setTimeout = originalSetTimeout
assert.deepEqual(resumeRequests, [
  '/api/threads/thread/messages/message/stream',
  '/api/threads/thread/messages/message/stream',
])
assert.deepEqual(resumeCredentials, ['same-origin', 'same-origin'])
assert.deepEqual(resumeAuthorization, [null, null])
assert.equal(resumedMessageId, 'message')

let regenerateBody: unknown
let regenerateCredentials: RequestCredentials | undefined
let regenerateAuthorization: string | null = null
globalThis.fetch = async (_input, init) => {
  regenerateBody = JSON.parse(String(init?.body))
  regenerateCredentials = init?.credentials
  regenerateAuthorization = new Headers(init?.headers).get('Authorization')
  return new Response('data: {"type":"done","data":null}\n\n')
}
await regenerateStream('thread-1', 'rag', 'high', true, {
  onToken: () => {},
  onThinkingStep: () => {},
  onSource: () => {},
  onSources: () => {},
  onSearchQueries: () => {},
  onComplete: () => {},
  onError: (error) => { throw error },
})
assert.deepEqual(regenerateBody, { mode: 'rag', reasoning: 'high', allow_remote: true })
assert.equal(regenerateCredentials, 'same-origin')
assert.equal(regenerateAuthorization, null)

const failedRegenerationMessages = [
  {
    message_id: 'regen-user',
    role: 'user' as const,
    content: 'question before regeneration',
    timestamp: 1,
  },
  {
    message_id: 'regen-old-assistant',
    role: 'assistant' as const,
    content: 'answer before regeneration',
    timestamp: 2,
  },
]
useChatStore.getState().switchThread('regen-error-thread', failedRegenerationMessages)
let failedRegenerationRequests = 0
globalThis.fetch = async () => {
  failedRegenerationRequests += 1
  return new Response(
    `data: ${JSON.stringify({
      type: 'accepted',
      data: {
        thread_id: 'regen-error-thread',
        message_id: 'regen-new-assistant',
        user_message_id: 'regen-user',
      },
    })}\n\n` +
    `data: ${JSON.stringify({
      type: 'error',
      data: { message: 'regeneration relay failed', error_id: 'regen-error' },
    })}\n\n`
  )
}
await useChatStore.getState().regenerate('simple', 'none', false)
assert.equal(failedRegenerationRequests, 1)
assert.deepEqual(useChatStore.getState().messages, failedRegenerationMessages)
assert.deepEqual(useChatStore.getState().streaming, {
  isStreaming: false,
  currentMessage: '',
  thinkingSteps: [],
  sources: [],
  searchQueries: [],
})
assert.equal(useChatStore.getState().isLoading, false)
assert.equal(useChatStore.getState().abortController, null)
assert.equal(useChatStore.getState().error?.message, 'regeneration relay failed')

globalThis.fetch = async () => new Response(JSON.stringify({
  success: false,
  block_key: 'title',
  message: 'generation failed',
}), { status: 200, headers: { 'Content-Type': 'application/json' } })
await assert.rejects(
  regenerateSummaryBlock('content', 'title'),
  /generation failed/
)


const threadAMessages = [
  { role: 'user' as const, content: 'A question', timestamp: 1 },
  { role: 'assistant' as const, content: 'A answer', timestamp: 2 },
]
const threadBMessages = [
  { role: 'user' as const, content: 'B question', timestamp: 3 },
  { role: 'assistant' as const, content: 'B answer', timestamp: 4 },
]
useChatStore.getState().switchThread('thread-a', threadAMessages)
let markRequestStarted!: () => void
const requestStarted = new Promise<void>((resolve) => { markRequestStarted = resolve })
globalThis.fetch = async (_input, init) => {
  markRequestStarted()
  return new Promise<Response>((_resolve, reject) => {
    const abort = () => reject(new DOMException('Aborted', 'AbortError'))
    if (init?.signal?.aborted) abort()
    else init?.signal?.addEventListener('abort', abort, { once: true })
  })
}
const regeneration = useChatStore.getState().regenerate('simple', 'none', false)
await requestStarted
useChatStore.getState().switchThread('thread-b', threadBMessages)
await regeneration
assert.equal(useChatStore.getState().threadId, 'thread-b')
assert.deepEqual(useChatStore.getState().messages, threadBMessages)

globalThis.fetch = originalFetch
