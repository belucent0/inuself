/**
 * Thread API 엔드포인트
 *
 * SOLID 원칙:
 * - Single Responsibility: Thread 관련 API 호출만 담당
 * - Open/Closed: 새로운 엔드포인트 추가 시 확장 가능
 */

import { httpClient } from '../api/httpClient'
import type {
  Thread,
  ThreadListResponse,
  CreateThreadRequest,
  SendMessageRequest,
} from '@/shared/types'

/**
 * 스레드 목록 조회
 */
export async function getThreads(
  limit = 50,
  offset = 0
): Promise<ThreadListResponse> {
  return httpClient.get<ThreadListResponse>(
    `/threads?limit=${limit}&offset=${offset}`
  )
}

/**
 * 특정 스레드 조회
 */
export async function getThread(threadId: string): Promise<Thread> {
  return httpClient.get<Thread>(`/threads/${threadId}`)
}

/**
 * 새 스레드 생성
 */
export async function createThread(
  data: CreateThreadRequest
): Promise<Thread> {
  return httpClient.post<Thread>('/threads', data)
}

/**
 * 스레드 삭제
 */
export async function deleteThread(threadId: string): Promise<void> {
  return httpClient.delete<void>(`/threads/${threadId}`)
}

/**
 * 스레드 제목 업데이트
 */
export async function updateThreadTitle(
  threadId: string,
  title: string
): Promise<Thread> {
  return httpClient.patch<Thread>(`/threads/${threadId}`, { title })
}

/**
 * 새 스레드 생성 및 첫 메시지 전송 (SSE 스트리밍)
 *
 * @returns ReadableStream for SSE events
 */
export async function createThreadWithMessage(
  data: SendMessageRequest
): Promise<ReadableStream<Uint8Array>> {
  return httpClient.postStream('/threads/stream', {
    query: data.content,
    mode: data.mode || 'auto',
    context: data.metadata,
    model: data.model,
  })
}

/**
 * 기존 스레드에 메시지 추가 (SSE 스트리밍)
 *
 * @returns ReadableStream for SSE events
 */
export async function sendMessage(
  threadId: string,
  data: SendMessageRequest
): Promise<ReadableStream<Uint8Array>> {
  return httpClient.postStream(`/threads/${threadId}/messages/stream`, {
    query: data.content,
    mode: data.mode || 'auto',
    context: data.metadata,
    model: data.model,
  })
}

/**
 * 메시지 재생성 (SSE 스트리밍)
 *
 * @returns ReadableStream for SSE events
 */
export async function regenerateMessage(
  threadId: string,
  mode?: string,
  model?: string
): Promise<ReadableStream<Uint8Array>> {
  return httpClient.postStream(`/threads/${threadId}/regenerate`, {
    mode: mode || 'auto',
    model,
  })
}

/**
 * Thread API 서비스 객체
 */
export const threadsApi = {
  getThreads,
  getThread,
  createThread,
  deleteThread,
  updateThreadTitle,
  createThreadWithMessage,
  sendMessage,
  regenerateMessage,
}

export default threadsApi
