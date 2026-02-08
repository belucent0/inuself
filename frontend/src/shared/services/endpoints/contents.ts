/**
 * Contents API 엔드포인트
 */

import { httpClient } from '../api/httpClient'
import type { ContentSummary, ContentDetail } from '@/features/content/types'

// 백엔드 API 응답 구조
interface ApiContentListResponse {
  items: ContentSummary[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

// 프론트엔드에서 사용하는 구조
export interface ContentListResponse {
  contents: ContentSummary[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface ContentListParams {
  page?: number
  pageSize?: number
  status?: string
  contentType?: string
}

/**
 * 콘텐츠 목록 조회
 */
export async function getContents(params?: ContentListParams): Promise<ContentListResponse> {
  const queryParams = new URLSearchParams()
  if (params?.page) queryParams.set('page', params.page.toString())
  if (params?.pageSize) queryParams.set('page_size', params.pageSize.toString())
  if (params?.status) queryParams.set('status', params.status)
  if (params?.contentType) queryParams.set('content_type', params.contentType)

  const query = queryParams.toString()
  const response = await httpClient.get<ApiContentListResponse>(`/contents${query ? `?${query}` : ''}`)

  // API 응답 구조를 프론트엔드 구조로 변환 (items -> contents)
  return {
    contents: response.items,
    total: response.total,
    page: response.page,
    page_size: response.page_size,
    total_pages: response.total_pages,
  }
}

/**
 * 콘텐츠 상세 조회
 */
export async function getContent(id: string): Promise<ContentDetail> {
  return httpClient.get<ContentDetail>(`/contents/${id}`)
}

/**
 * 콘텐츠 삭제 (벌크)
 */
export async function deleteContents(ids: string[]): Promise<{ message: string }> {
  return httpClient.post<{ message: string }>('/contents/bulk-delete', { content_ids: ids })
}

/**
 * 콘텐츠 재처리
 */
export async function retryProcessing(
  id: string,
  type: 'download' | 'asr' | 'ocr' | 'summary',
  options?: {
    minSpeakers?: number
    maxSpeakers?: number
    ocrMode?: string
    accuracyMode?: string
  }
): Promise<{ message: string }> {
  return httpClient.post<{ message: string }>(`/contents/${id}/retry`, {
    type,
    ...options,
  })
}

export const contentsApi = {
  getContents,
  getContent,
  deleteContents,
  retryProcessing,
}
