import { httpClient } from '../api/httpClient'
import type { UploadOptions, UploadResponse } from '@/features/upload/types'

export function uploadContent(
  file: File,
  options?: UploadOptions
): Promise<UploadResponse> {
  const formData = new FormData()
  formData.append('file', file)

  const params = new URLSearchParams()
  if (options?.minSpeakers !== undefined) params.append('min_speakers', String(options.minSpeakers))
  if (options?.maxSpeakers !== undefined) params.append('max_speakers', String(options.maxSpeakers))
  if (options?.ocrMode !== undefined) params.append('ocr_mode', options.ocrMode)
  if (options?.ocrAccuracyMode !== undefined) params.append('ocr_accuracy_mode', options.ocrAccuracyMode)
  if (options?.accuracyMode !== undefined) params.append('accuracy_mode', options.accuracyMode)

  const query = params.toString()
  return httpClient.postForm<UploadResponse>(
    `/contents/upload${query ? `?${query}` : ''}`,
    formData
  )
}

export function uploadYouTubeContent(url: string): Promise<UploadResponse> {
  return httpClient.post<UploadResponse>('/contents/upload-youtube', { url })
}

export const uploadApi = {
  uploadContent,
  uploadYouTubeContent,
}
