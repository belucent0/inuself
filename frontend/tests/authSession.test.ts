import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'

import {
  AUTH_UNAUTHORIZED_EVENT,
  httpClient,
  isApiUnavailable,
  type ApiError,
} from '../src/shared/services/api/httpClient'
import { authApi, type AuthUser } from '../src/shared/services/endpoints/auth'

const originalFetch = globalThis.fetch
const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'window')
const browserEvents = new EventTarget()
Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: browserEvents,
})

let unauthorizedEvents = 0
browserEvents.addEventListener(AUTH_UNAUTHORIZED_EVENT, () => {
  unauthorizedEvents += 1
})

const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = []
let nextFetch: () => Promise<Response>
globalThis.fetch = (async (input, init) => {
  calls.push({ input, init })
  return nextFetch()
}) as typeof fetch

function respond(response: Response): void {
  nextFetch = async () => response
}

function fail(error: Error): void {
  nextFetch = async () => { throw error }
}

function lastCall() {
  const call = calls.at(-1)
  assert.ok(call)
  return call
}

try {
  assert.equal(isApiUnavailable(new TypeError('network unavailable')), true)
  assert.equal(isApiUnavailable({ status: 503 }), true)
  assert.equal(isApiUnavailable({ status: 401 }), false)

  respond(Response.json({ ok: true }))
  await httpClient.post('/protected', { value: 1 })
  let call = lastCall()
  assert.equal(call.input, '/api/protected')
  assert.equal(call.init?.credentials, 'same-origin')
  assert.equal(new Headers(call.init?.headers).has('Authorization'), false)
  assert.deepEqual(JSON.parse(String(call.init?.body)), { value: 1 })

  respond(Response.json({ uploaded: true }))
  const form = new FormData()
  form.append('file', 'contents')
  await httpClient.postForm('/contents/upload', form)
  call = lastCall()
  assert.equal(call.init?.credentials, 'same-origin')
  assert.equal(call.init?.body, form)
  assert.equal(new Headers(call.init?.headers).has('Content-Type'), false)
  assert.equal(new Headers(call.init?.headers).has('Authorization'), false)

  respond(new Response('stream'))
  const stream = await httpClient.postStream('/stream', { query: 'hello' })
  assert.ok(stream)
  call = lastCall()
  assert.equal(call.init?.credentials, 'same-origin')
  assert.equal(new Headers(call.init?.headers).has('Authorization'), false)

  const callsBeforeUnauthorized = calls.length
  respond(Response.json({ detail: 'unauthorized' }, { status: 401 }))
  await assert.rejects(
    httpClient.get('/protected'),
    (error: ApiError) => error.status === 401
  )
  assert.equal(calls.length, callsBeforeUnauthorized + 1)
  assert.equal(unauthorizedEvents, 1)

  respond(Response.json({ detail: 'unauthorized' }, { status: 401 }))
  await assert.rejects(httpClient.get('/auth/me', { reportUnauthorized: false }))
  assert.equal(unauthorizedEvents, 1)

  respond(Response.json({ detail: 'unavailable' }, { status: 503 }))
  await assert.rejects(httpClient.get('/protected'))
  assert.equal(unauthorizedEvents, 1)

  respond(Response.json({ detail: 'unauthorized' }, { status: 401 }))
  await httpClient.verifySessionAfterStreamError()
  assert.equal(unauthorizedEvents, 2)

  respond(Response.json({ detail: 'unavailable' }, { status: 503 }))
  await httpClient.verifySessionAfterStreamError()
  assert.equal(unauthorizedEvents, 2)

  fail(new TypeError('network unavailable'))
  await assert.rejects(httpClient.get('/protected'))
  assert.equal(unauthorizedEvents, 2)

  const user: AuthUser = {
    id: 'user-1',
    login_id: 'tester',
    name: null,
    is_active: true,
    is_super: false,
    created_at: '2026-08-22T00:00:00Z',
  }
  respond(Response.json({ user }))
  assert.deepEqual(await authApi.login({ login_id: 'tester', password: 'secret' }), { user })
  call = lastCall()
  assert.equal(call.input, '/api/auth/login')
  assert.deepEqual(JSON.parse(String(call.init?.body)), { login_id: 'tester', password: 'secret' })

  respond(Response.json({ user }))
  assert.deepEqual(await authApi.signup({
    login_id: 'tester',
    password: 'secret',
    signup_code: 'code',
  }), { user })

  respond(new Response(null, { status: 204 }))
  await authApi.logout()
  call = lastCall()
  assert.equal(call.input, '/api/auth/logout')
  assert.equal(call.init?.body, undefined)

  const chatPage = readFileSync(path.join(process.cwd(), 'src/pages/ChatPage.tsx'), 'utf8')
  const contentChat = readFileSync(path.join(process.cwd(), 'src/shared/hooks/useContentChat.ts'), 'utf8')
  const fileProgress = readFileSync(path.join(process.cwd(), 'src/shared/hooks/useFileProgressSSE.ts'), 'utf8')
  assert.equal(chatPage.includes('access_token'), false)
  assert.equal(contentChat.includes('access_token'), false)
  assert.equal(fileProgress.includes('setTimeout'), false)
} finally {
  globalThis.fetch = originalFetch
  if (windowDescriptor) {
    Object.defineProperty(globalThis, 'window', windowDescriptor)
  } else {
    delete (globalThis as { window?: unknown }).window
  }
}
