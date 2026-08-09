import assert from 'node:assert/strict'

import { processSSEStream } from '../src/shared/services/chatStreamService'

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
