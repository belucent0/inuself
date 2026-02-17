/**
 * Upload API 엔드포인트
 */

import { httpClient, getBaseUrl } from '../api/httpClient'
import { getAccessToken } from '../authToken'
import type { UploadOptions, UploadResponse } from '@/features/upload/types'

/**
 * 파일 업로드
 */
export async function uploadContent(
  file: File,
  options?: UploadOptions
): Promise<UploadResponse> {
  const formData = new FormData()
  formData.append('file', file)

  const params = new URLSearchParams()
  if (options?.minSpeakers !== undefined) {
    params.append('min_speakers', options.minSpeakers.toString())
  }
  if (options?.maxSpeakers !== undefined) {
    params.append('max_speakers', options.maxSpeakers.toString())
  }
  if (options?.ocrMode !== undefined) {
    params.append('ocr_mode', options.ocrMode)
  }
  if (options?.ocrAccuracyMode !== undefined) {
    params.append('ocr_accuracy_mode', options.ocrAccuracyMode)
  }
  if (options?.accuracyMode !== undefined) {
    params.append('accuracy_mode', options.accuracyMode)
  }

  const queryString = params.toString()
  const baseUrl = getBaseUrl()
  const url = queryString
    ? `${baseUrl}/contents/upload?${queryString}`
    : `${baseUrl}/contents/upload`

  // JWT 토큰 헤더 추가
  const headers: HeadersInit = {}
  const accessToken = getAccessToken()
  if (accessToken) {
    headers['Authorization'] = `Bearer ${accessToken}`
  }

  const response = await fetch(url, {
    method: 'POST',
    headers,
    body: formData,
  })

  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(`업로드 실패: ${response.status} ${errorText}`)
  }

  return response.json()
}

/**
 * YouTube URL로 콘텐츠 업로드
 */
export async function uploadYouTubeContent(url: string): Promise<UploadResponse> {
  const response = await httpClient.post<UploadResponse>('/contents/upload-youtube', { url })
  return response
}

/**
 * WebSocket Base URL 반환
 */
export function getWebSocketBase(): string {
  if (typeof window === 'undefined') return ''

  // 로컬 개발 환경 감지
  if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    return 'ws://localhost:8000/ws'
  }

  // 기본: 상대 경로 (Nginx 프록시 사용)
  return '/ws'
}

export const uploadApi = {
  uploadContent,
  uploadYouTubeContent,
  getWebSocketBase,
}
