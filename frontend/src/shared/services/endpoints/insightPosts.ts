import { httpClient } from '../api/httpClient'
import type {
  InsightPostCreateRequest,
  InsightPostDetail,
  InsightPostListResponse,
  InsightPostUpdateRequest,
  InsightResearchRequest,
} from '@/features/insight'

export interface InsightPostListParams {
  page?: number
  pageSize?: number
}

export async function getInsightPosts(params?: InsightPostListParams): Promise<InsightPostListResponse> {
  const queryParams = new URLSearchParams()
  if (params?.page) queryParams.set('page', String(params.page))
  if (params?.pageSize) queryParams.set('page_size', String(params.pageSize))

  const query = queryParams.toString()
  return httpClient.get<InsightPostListResponse>(`/insight-posts${query ? `?${query}` : ''}`)
}

export async function getInsightPost(id: string): Promise<InsightPostDetail> {
  return httpClient.get<InsightPostDetail>(`/insight-posts/${id}`)
}

export async function createInsightPostFromContent(
  contentId: string,
  payload: InsightPostCreateRequest
): Promise<InsightPostDetail> {
  return httpClient.post<InsightPostDetail>(`/insight-posts/from-content/${contentId}`, payload)
}

export async function updateInsightPost(
  id: string,
  payload: InsightPostUpdateRequest
): Promise<InsightPostDetail> {
  return httpClient.patch<InsightPostDetail>(`/insight-posts/${id}`, payload)
}

export async function regenerateInsightPost(
  id: string,
  payload: InsightPostCreateRequest
): Promise<InsightPostDetail> {
  return httpClient.post<InsightPostDetail>(`/insight-posts/${id}/regenerate`, payload)
}

export async function researchInsightPost(
  id: string,
  payload: InsightResearchRequest
): Promise<InsightPostDetail> {
  return httpClient.post<InsightPostDetail>(`/insight-posts/${id}/research`, payload)
}

export const insightPostsApi = {
  getInsightPosts,
  getInsightPost,
  createInsightPostFromContent,
  updateInsightPost,
  regenerateInsightPost,
  researchInsightPost,
}
