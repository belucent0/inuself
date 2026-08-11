import assert from 'node:assert/strict'

import { processSSEStream } from '../src/shared/services/chatStreamService'

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
