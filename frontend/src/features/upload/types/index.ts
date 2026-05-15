/**
 * Upload Feature 타입 정의
 */

// 화자 수 범위
export type SpeakerRange = 'auto' | '1-2' | '3-6' | '7-10' | '11+' | null

// OCR 처리 모드
export type OcrMode = 'portray' | 'document' | null

// 정확도 모드
export type AccuracyMode = 'speed' | 'accuracy'

// 파일 처리 상태 (SSE)
export type FileStatus =
  | 'QUEUED'
  | 'PULLING'
  | 'PROCESSING'
  | 'OCR_PROCESSING'
  | 'SUMMARY_QUEUED'
  | 'SUMMARIZING'
  | 'COMPLETED'
  | 'DOWNLOAD_FAILED'
  | 'ASR_FAILED'
  | 'OCR_FAILED'
  | 'SUMMARY_FAILED'
  | 'FAILED'
  | 'CANCELLED'

// 파일 처리 단계 (WebSocket)
export type FileProgressStep =
  | 'youtube_download'
  | 'uploading'
  | 'download_complete'
  | 'asr_pipeline_start'
  | 'asr_pipeline_complete'
  | 'asr_complete'
  | 'asr'
  | 'asr_completed'
  | 'asr_failed'
  | 'ocr'
  | 'ocr_completed'
  | 'ocr_failed'
  | 'llm'
  | 'llm_failed'
  | 'summary_queued'
  | 'completed'
  | 'error'

// 파일 진행 상태 이벤트
export interface FileProgressEvent {
  type: 'file_progress' | 'connection' | 'pong' | 'content_created'
  file_id?: string
  content_id?: string
  trace_id?: string
  filename?: string
  content_type?: string
  status?: FileStatus | string
  step?: FileProgressStep
  progress?: number
  message?: string
  timestamp?: string
  channel?: string
  metadata?: {
    // ASR/upload 메타
    title?: string
    duration_seconds?: number
    speakers?: string[]
    page_count?: number
    // 공통: backend publish_file_progress가 발행하는 step 구분자
    event_subtype?:
      | 'block_completed'
      | 'summary_finalized'
      | 'block_regenerated'
      | 'block_regenerate_finalized'
      | 'chunk_completed'
      | 'translation_finalized'
      | string
    // PR-B/C 요약 block 진행
    block_key?: string
    block_label?: string
    block_status?: string
    blocks_done?: number
    blocks_failed?: number
    blocks_total?: number
    template_id?: string
    regenerate_target?: string
    // PR-Translate 번역 청크 진행
    target_lang?: string
    chunk_idx?: number
    chunk_status?: string
    chunks_done?: number
    chunks_failed?: number
    chunks_total?: number
    success?: boolean
  }
}

// 파일 진행 상태
export interface FileProgress {
  fileId: string | null
  status: FileStatus
  step: FileProgressStep | null
  progress: number
  message: string
  lastUpdate: Date | null
  isConnected: boolean
}

// 업로드 옵션
export interface UploadOptions {
  minSpeakers?: number
  maxSpeakers?: number
  ocrMode?: string
  ocrAccuracyMode?: AccuracyMode
  accuracyMode?: AccuracyMode
}

// 업로드 응답
export interface UploadResponse {
  content_id: string
}

// 지원 파일 확장자
export const AUDIO_EXTENSIONS = [
  '.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.wma',
  '.mp4', '.avi', '.mkv', '.mov', '.webm'
]

export const DOCUMENT_EXTENSIONS = [
  '.txt', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'
]

export const OFFICE_EXTENSIONS = [
  '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'
]

// 파일 타입 체크 유틸리티
export function isAudioFile(filename: string): boolean {
  const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
  return AUDIO_EXTENSIONS.includes(ext)
}

export function isDocumentFile(filename: string): boolean {
  const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
  return DOCUMENT_EXTENSIONS.includes(ext)
}

export function isOfficeDocument(filename: string): boolean {
  const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
  return OFFICE_EXTENSIONS.includes(ext)
}

// 화자 수 범위 옵션
export const SPEAKER_RANGE_OPTIONS = [
  { value: 'auto', label: '자동 파악' },
  { value: '1-2', label: '1~2명' },
  { value: '3-6', label: '3~6명' },
  { value: '7-10', label: '7~10명' },
  { value: '11+', label: '11명 이상' },
] as const

// 화자 범위를 min/max 값으로 변환
export function getSpeakerRange(range: SpeakerRange): { min?: number; max?: number } {
  switch (range) {
    case 'auto':
      return {}
    case '1-2':
      return { min: 1, max: 2 }
    case '3-6':
      return { min: 3, max: 6 }
    case '7-10':
      return { min: 7, max: 10 }
    case '11+':
      return { min: 11 }
    default:
      return {}
  }
}
