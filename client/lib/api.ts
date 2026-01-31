const getApiBase = (): string => {
  if (typeof window === 'undefined') {
    // 서버 사이드: 완전한 URL 필요
    return process.env.API_BASE_URL || 'http://localhost:8000/api'
  }
  // 클라이언트 사이드: 상대 경로 사용 가능
  return process.env.NEXT_PUBLIC_API_BASE_URL || '/api'
}

const API_BASE = getApiBase()

export type SttLog = {
  id: string  // UUID v7
  message?: string
  created_at: string
  log: Record<string, unknown>
}

export type ContentStatus =
  | 'QUEUED' // 처리 대기중 (큐에 등록됨)
  | 'PROCESSING' // 처리중 (ASR/화자분리 진행 중)
  | 'OCR_PROCESSING' // OCR 처리 중
  | 'SUMMARY_QUEUED' // LLM 요약 대기중 (큐에 등록됨)
  | 'SUMMARIZING' // LLM 요약 진행 중
  | 'COMPLETED' // 전체 파이프라인 완료
  | 'ASR_FAILED' // ASR/화자분리 단계 실패
  | 'OCR_FAILED' // OCR 처리 실패
  | 'SUMMARY_FAILED' // LLM 요약 실패
  | 'CANCELLED' // 취소됨 (사용자 취소 또는 타임아웃)

export type ContentType = 'AUDIO' | 'DOCUMENT' | 'PORTRAY'

export type ContentSummary = {
  id: string  // File.id (UUID v7)
  public_id: string | null  // Content.id (UUID v7) - 하위 호환성
  filename: string
  object_key: string
  media_url?: string | null
  speakers: string[]
  duration_seconds: number
  status: ContentStatus
  summary_md?: string | null
  title?: string | null
  created_at: string
  updated_at?: string | null
  content_type?: ContentType // 파일 타입 (선택적, 하위 호환성)
  transcription?: {
    id: number
    file_id: number
    speakers: string[]
    duration_seconds: number
    transcription: Record<string, unknown>
  } | null
  document?: {
    id: number
    file_id: number
    ocr_text: string
    page_count: number
    ocr_metadata: Record<string, unknown>
    html_content?: string | null  // Docling HTML 출력 (뷰어용)
  } | null
}

export type LlmLog = {
  id: string  // UUID v7
  message?: string
  created_at: string
  log: Record<string, unknown>
}

export type ContentDetail = ContentSummary & {
  transcription: {
    text: string
    segments: Array<{
      id: number
      start: number
      end: number
      text: string
      speaker?: string
    }>
    diarization_metadata?: {
      num_speakers: number
      speaker_labels: string[]
      speaker_embeddings?: Record<string, number[]>  // 화자별 embedding 벡터
      segment_embeddings?: Array<{  // 시간대별 세그먼트 embedding 벡터
        start: number
        end: number
        speaker: string
        duration: number
        embedding: number[]
      }>
    }
  } | null
  document?: {
    id: number
    file_id: number
    ocr_text: string
    page_count: number
    ocr_metadata: Record<string, unknown>
    html_content?: string | null  // Docling HTML 출력 (뷰어용)
  } | null
  logs: SttLog[]
  llm_logs: LlmLog[]
}

export type ContentListResponse = {
  items: ContentSummary[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export async function listContents(page: number = 1, pageSize: number = 20): Promise<ContentListResponse> {
  const res = await fetch(`${API_BASE}/contents?page=${page}&page_size=${pageSize}`, { cache: 'no-store' })
  if (!res.ok) {
    throw new Error('콘텐츠 목록 조회 실패')
  }
  return res.json()
}

export async function getContentDetail(id: string): Promise<ContentDetail | null> {
  // 항상 File.id (UUID)로 직접 조회
  const endpoint = `${API_BASE}/contents/${id}`

  const res = await fetch(endpoint, { cache: 'no-store' })
  if (res.status === 404) {
    return null
  }
  if (!res.ok) {
    throw new Error('콘텐츠 상세 조회 실패')
  }
  return res.json()
}

export async function deleteQueuedContents(): Promise<{ deleted_count: number; message: string }> {
  const res = await fetch(`${API_BASE}/contents/queued`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    const errorText = await res.text()
    throw new Error(`삭제 실패: ${res.status} ${errorText}`)
  }
  return res.json()
}

export type BulkDeleteResponse = {
  deleted_count: number
  deleted_ids: string[]  // UUID
  skipped_ids: string[]  // UUID
  message: string
}

export async function deleteContentsBulk(contentIds: string[]): Promise<BulkDeleteResponse> {
  const res = await fetch(`${API_BASE}/contents/bulk-delete`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ content_ids: contentIds }),
  })

  if (!res.ok) {
    const errorText = await res.text()
    throw new Error(`선택 삭제 실패: ${res.status} ${errorText}`)
  }

  return res.json()
}

export async function uploadContent(
  file: File,
  minSpeakers?: number,
  maxSpeakers?: number,
  ocrMode?: string,
  ocrAccuracyMode?: 'speed' | 'accuracy',
  accuracyMode?: 'speed' | 'accuracy'
): Promise<{ content_id: string; public_id: string | null }> {
  const formData = new FormData()
  formData.append('file', file)

  const params = new URLSearchParams()
  if (minSpeakers !== undefined) {
    params.append('min_speakers', minSpeakers.toString())
  }
  if (maxSpeakers !== undefined) {
    params.append('max_speakers', maxSpeakers.toString())
  }
  if (ocrMode !== undefined) {
    params.append('ocr_mode', ocrMode)
  }
  if (ocrAccuracyMode !== undefined) {
    params.append('ocr_accuracy_mode', ocrAccuracyMode)
  }
  if (accuracyMode !== undefined) {
    params.append('accuracy_mode', accuracyMode)
  }

  const queryString = params.toString()
  const url = queryString
    ? `${API_BASE}/contents/upload?${queryString}`
    : `${API_BASE}/contents/upload`

  try {
    const res = await fetch(url, {
      method: 'POST',
      body: formData,
    })

    console.log('Upload response status:', res.status)

    if (!res.ok) {
      const errorText = await res.text()
      console.error('Upload failed:', res.status, errorText)
      throw new Error(`업로드 실패: ${res.status} ${errorText}`)
    }

    const contentType = res.headers.get('content-type')
    console.log('Response content-type:', contentType)

    let data
    try {
      data = await res.json()
      console.log('Upload success, data:', data)
    } catch (jsonError) {
      const text = await res.text()
      console.error('JSON parse error:', jsonError, 'Response text:', text)
      throw new Error(`응답 파싱 실패: ${text}`)
    }

    return data
  } catch (error) {
    console.error('Upload error:', error)
    if (error instanceof Error) {
      throw error
    }
    throw new Error('업로드 중 알 수 없는 오류가 발생했습니다.')
  }
}

export async function retryProcessing(
  contentId: string,  // UUID
  type: 'asr' | 'summary' | 'ocr',
  minSpeakers?: number,
  maxSpeakers?: number,
  ocrMode?: string,
  ocrAccuracyMode?: 'speed' | 'accuracy',
  accuracyMode?: 'speed' | 'accuracy'
): Promise<{ success: boolean; message: string; job_id?: string }> {
  const params = new URLSearchParams({ type })
  if (minSpeakers !== undefined) {
    params.append('min_speakers', minSpeakers.toString())
  }
  if (maxSpeakers !== undefined) {
    params.append('max_speakers', maxSpeakers.toString())
  }
  if (ocrMode !== undefined) {
    params.append('ocr_mode', ocrMode)
  }
  if (ocrAccuracyMode !== undefined) {
    params.append('ocr_accuracy_mode', ocrAccuracyMode)
  }
  if (accuracyMode !== undefined) {
    params.append('accuracy_mode', accuracyMode)
  }

  const res = await fetch(`${API_BASE}/contents/${contentId}/retry?${params.toString()}`, {
    method: 'POST',
  })
  if (!res.ok) {
    const errorText = await res.text()
    throw new Error(`재처리 실패: ${res.status} ${errorText}`)
  }
  return res.json()
}

export async function reclusterSpeakers(
  contentId: string,  // UUID
  numSpeakers?: number,
  similarityThreshold?: number
): Promise<{ message: string; num_speakers: number; speaker_labels: string[]; updated_segments_count: number }> {
  const body: { num_speakers?: number; similarity_threshold?: number } = {}
  if (numSpeakers !== undefined) {
    body.num_speakers = numSpeakers
  }
  if (similarityThreshold !== undefined) {
    body.similarity_threshold = similarityThreshold
  }

  const res = await fetch(`${API_BASE}/contents/${contentId}/recluster-speakers`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })

  if (!res.ok) {
    const errorText = await res.text()
    throw new Error(`재클러스터링 실패: ${res.status} ${errorText}`)
  }

  return res.json()
}


// ... (기존 코드 유지)

/**
 * WebSocket Base URL 반환
 * 
 * 로컬 개발 환경(localhost)에서는 백엔드 포트(8000)로 직접 연결하여
 * Next.js 개발 서버의 프록시 이슈를 우회합니다.
 * 프로덕션 환경에서는 Nginx 프록시를 타도록 상대 경로('/ws')를 반환합니다.
 */
export const getWebSocketBase = (): string => {
  if (typeof window === 'undefined') return ''

  // 로컬 개발 환경 감지
  if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
    return 'ws://localhost:8000/ws'
  }

  // 기본: 상대 경로 (Nginx 프록시 사용)
  return '/ws'
}


/**
 * YouTube URL로 콘텐츠 업로드 (비동기 다운로드)
 * 
 * @param url YouTube 영상 URL
 * @returns content_id가 포함된 응답
 */
export async function uploadYouTubeContent(url: string): Promise<{ content_id: string; public_id?: string | null }> {
  const res = await fetch(`${API_BASE}/contents/upload-youtube`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ url }),
  })

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}))
    if (res.status === 400) {
      if (errorData.detail?.includes('duration')) {
        throw new Error('1시간 이내의 영상만 처리할 수 있습니다')
      }
      throw new Error('유효한 유튜브 링크가 아닙니다')
    }
    throw new Error(errorData.detail || '업로드에 실패했습니다')
  }

  return res.json()
}


