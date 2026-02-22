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
} from '@/shared/types'

/**
 * 스레드 목록 조회
 * - content_id 지정 시 해당 콘텐츠의 스레드만 조회 (메시지 포함)
 */
export async function getThreads(
  params: { limit?: number; offset?: number; content_id?: string } = {}
): Promise<ThreadListResponse> {
  const { limit = 50, offset = 0, content_id } = params
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (content_id) query.set('content_id', content_id)
  return httpClient.get<ThreadListResponse>(`/threads?${query}`)
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
 * 스레드 메타데이터 부분 업데이트 (기존 값과 병합)
 */
export async function updateThreadMetadata(
  threadId: string,
  metadata: Record<string, unknown>
): Promise<void> {
  return httpClient.patch<void>(`/threads/${threadId}/metadata`, { metadata })
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
 * 다중 스레드 일괄 삭제
 */
export async function bulkDeleteThreads(threadIds: string[]): Promise<{
  deleted_count: number
  deleted_ids: string[]
  skipped_ids: string[]
  message: string
}> {
  return httpClient.post('/threads/bulk-delete', { thread_ids: threadIds })
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
  updateThreadMetadata,
  regenerateMessage,
  bulkDeleteThreads,
}

export default threadsApi
