const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api'

export type SttLog = {
  id: number
  message?: string
  created_at: string
  log: Record<string, unknown>
}

export type ContentStatus = 
  | 'QUEUED' // 처리 대기중 (큐에 등록됨)
  | 'PROCESSING' // 처리중 (ASR/화자분리 진행 중)
  | 'SUMMARIZING' // LLM 요약 진행 중
  | 'COMPLETED' // 전체 파이프라인 완료
  | 'FAILED' // ASR/화자분리 단계 실패
  | 'SUMMARY_FAILED' // LLM 요약 실패
  | 'CANCELLED' // 취소됨 (사용자 취소 또는 타임아웃)
  | 'RETRYING' // 재시도 중 (실패 후 자동 재시도)

export type ContentSummary = {
  id: number
  filename: string
  object_key: string
  speakers: string[]
  duration_seconds: number
  status: ContentStatus
  summary_md?: string | null
  created_at: string
}

export type LlmLog = {
  id: number
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
  }
  logs: SttLog[]
  llm_logs: LlmLog[]
}

export async function listContents(): Promise<ContentSummary[]> {
  const res = await fetch(`${API_BASE}/contents`, { cache: 'no-store' })
  if (!res.ok) {
    throw new Error('콘텐츠 목록 조회 실패')
  }
  return res.json()
}

export async function getContentDetail(id: string): Promise<ContentDetail> {
  const res = await fetch(`${API_BASE}/contents/${id}`, { cache: 'no-store' })
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
  deleted_ids: number[]
  skipped_ids: number[]
  message: string
}

export async function deleteContentsBulk(contentIds: number[]): Promise<BulkDeleteResponse> {
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

export async function uploadContent(file: File): Promise<{ content_id: number }> {
  const formData = new FormData()
  formData.append('file', file)

  try {
    const res = await fetch(`${API_BASE}/contents/upload`, {
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

