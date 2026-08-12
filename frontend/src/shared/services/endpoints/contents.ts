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
  search?: string
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
  if (params?.search) queryParams.set('search', params.search)

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
  // Backend expects type as query parameter, others can be in body or query
  const params = new URLSearchParams({ type })
  if (options?.minSpeakers) params.append('min_speakers', String(options.minSpeakers))
  if (options?.maxSpeakers) params.append('max_speakers', String(options.maxSpeakers))
  if (options?.ocrMode) params.append('ocr_mode', options.ocrMode)
  if (options?.accuracyMode) params.append('accuracy_mode', options.accuracyMode)

  return httpClient.post<{ message: string }>(`/contents/${id}/retry?${params.toString()}`)
}

/**
 * 단일 summary block 재생성 (PR-C 부분 재생성)
 */
export async function regenerateSummaryBlock(
  id: string,
  blockKey: string,
): Promise<{ success: boolean; block_key: string; message: string }> {
  const result = await httpClient.post<{ success: boolean; block_key: string; message: string }>(
    `/contents/${id}/summary/blocks/${blockKey}/regenerate`
  )
  if (!result.success) throw new Error(result.message || 'Summary block regeneration failed')
  return result
}

/**
 * Transcript 청크 단위 한국어 번역 (PR-Translate.1)
 */
export async function translateContent(
  id: string,
  targetLang: string = 'ko',
): Promise<{
  status: 'accepted'
  target_lang: string
  message: string
}> {
  const params = new URLSearchParams({ target_lang: targetLang })
  return httpClient.post(`/contents/${id}/translate?${params.toString()}`)
}

export const contentsApi = {
  getContents,
  getContent,
  deleteContents,
  retryProcessing,
  regenerateSummaryBlock,
  translateContent,
}
