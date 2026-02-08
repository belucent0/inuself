/**
 * Content Feature 타입 정의
 */

export type ContentType = 'DOCUMENT' | 'AUDIO' | 'PORTRAY'

export type ContentStatus =
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
  | 'CANCELLED'

export interface ContentSummary {
  id: string
  filename: string
  title: string
  content_type: ContentType
  status: ContentStatus
  created_at: string
  updated_at: string
  speakers: string[]
  duration_seconds: number
  transcription?: {
    duration_seconds?: number
    speakers?: string[]
  }
  document?: {
    page_count?: number
  }
}

export interface ContentDetail extends ContentSummary {
  file_url?: string
  media_url?: string
  summary?: string
  summary_html?: string
  summary_md?: string
  transcription?: TranscriptionData
  document?: DocumentData
  ocr_logs?: string[]
  llm_logs?: string[]
}

export interface TranscriptionData {
  segments: TranscriptionSegment[]
  speakers: string[]
  duration_seconds: number
  diarization_metadata?: {
    segment_embeddings?: number[][]
  }
}

export interface TranscriptionSegment {
  id: number
  text: string
  start: number
  end: number
  speaker?: string
}

export interface DocumentData {
  page_count: number
  text_content?: string
  ocr_text?: string
  html_content?: string
}

// 상태 레이블 매핑
export const STATUS_LABELS: Record<ContentStatus, string> = {
  QUEUED: '대기중',
  PULLING: '다운로드 중',
  PROCESSING: '인식중',
  OCR_PROCESSING: '인식중',
  SUMMARY_QUEUED: '요약 대기',
  SUMMARIZING: '요약중',
  COMPLETED: '완료',
  DOWNLOAD_FAILED: '다운로드 실패',
  ASR_FAILED: 'ASR 실패',
  OCR_FAILED: 'OCR 실패',
  SUMMARY_FAILED: '요약 실패',
  CANCELLED: '취소됨',
}

// 상태 배지 variant
export type BadgeVariant = 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info'

export const getStatusVariant = (status: ContentStatus): BadgeVariant => {
  switch (status) {
    case 'COMPLETED':
      return 'success'
    case 'DOWNLOAD_FAILED':
    case 'ASR_FAILED':
    case 'OCR_FAILED':
      return 'destructive'
    case 'SUMMARY_FAILED':
      return 'warning'
    case 'PULLING':
    case 'PROCESSING':
    case 'OCR_PROCESSING':
    case 'SUMMARIZING':
      return 'info'
    default:
      return 'outline'
  }
}

// 파일 유틸리티
export function getFileExtension(filename: string): string {
  const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
  return ext || ''
}

export function isAudioFile(filename: string): boolean {
  const audioExtensions = ['.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.wma']
  return audioExtensions.includes(getFileExtension(filename))
}

export function isVideoFile(filename: string): boolean {
  const videoExtensions = ['.mp4', '.avi', '.mkv', '.mov', '.webm']
  return videoExtensions.includes(getFileExtension(filename))
}

export function isDocumentFile(filename: string): boolean {
  const documentExtensions = ['.txt', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']
  return documentExtensions.includes(getFileExtension(filename))
}

export function isImageFile(filename: string): boolean {
  const imageExtensions = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp']
  return imageExtensions.includes(getFileExtension(filename))
}

export function isPdfFile(filename: string): boolean {
  return getFileExtension(filename) === '.pdf'
}
