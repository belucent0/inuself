import assert from 'node:assert/strict'

import { processSSEStream, regenerateStream } from '../src/shared/services/chatStreamService'

const events = [
  { type: 'token', data: 'missed' },
  { type: 'partial_restore', data: 'restored' },
  { type: 'token', data: '!' },
  { type: 'done', data: { content: 'authoritative' } },
]
const response = new Response(
  events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('')
)

let displayedContent = ''
let completedContent = ''
const result = await processSSEStream(response, 'simple', {
  onToken: (token) => { displayedContent += token },
  onContent: (content) => { displayedContent = content },
  onThinkingStep: () => {},
  onSource: () => {},
  onSources: () => {},
  onSearchQueries: () => {},
  onComplete: (message) => { completedContent = message.content },
  onError: (error) => { throw error },
})

assert.equal(displayedContent, 'restored!')
assert.equal(completedContent, 'authoritative')
assert.equal(result.content, 'authoritative')

const originalFetch = globalThis.fetch
const localStorageDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
let regenerateBody: unknown
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: { getItem: () => null },
})
globalThis.fetch = (async (_input, init) => {
  regenerateBody = JSON.parse(String(init?.body))
  return new Response('data: {"type":"done","data":null}\n\n')
}) as typeof fetch

try {
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
} finally {
  globalThis.fetch = originalFetch
  if (localStorageDescriptor) {
    Object.defineProperty(globalThis, 'localStorage', localStorageDescriptor)
  } else {
    delete (globalThis as { localStorage?: Storage }).localStorage
  }
}
