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
  /** SSE 전용 ephemeral 필드 (API 응답에는 없음) */
  progress?: number
  transcription?: {
    duration_seconds?: number
    speakers?: string[]
  }
  document?: {
    page_count?: number
  }
}

export type SummaryBlockStatus = 'pending' | 'in_progress' | 'success' | 'failed' | 'skipped'

export interface SummaryBlock {
  key: string
  label: string
  type: 'text' | 'list' | 'long_text' | string
  status: SummaryBlockStatus
  content: string | string[] | null
  attempts: number
  last_error?: string | null
  depends_on?: string[]
  completed_at?: string | null
}

export interface SummarySections {
  template_id: string
  started_at: string
  updated_at: string
  round: number
  blocks: SummaryBlock[]
}

export interface ContentDetail extends ContentSummary {
  file_url?: string
  media_url?: string
  summary?: string
  summary_html?: string
  summary_md?: string
  summary_sections?: SummarySections | null
  transcription?: TranscriptionData
  document?: DocumentData
  ocr_logs?: string[]
  llm_logs?: string[]
}

export interface TranslationProgress {
  active: boolean
  target_lang: string
  chunks_done: number
  chunks_failed: number
  chunks_total: number
  started_at?: string
  updated_at?: string
  success?: boolean
}

export interface TranscriptionData {
  segments: TranscriptionSegment[]
  speakers: string[]
  duration_seconds: number
  diarization_metadata?: {
    segment_embeddings?: number[][]
  }
  /** PR-Translate 새로고침 복원용 BG 진행 상태 */
  translation_progress?: TranslationProgress
}

export interface TranscriptionSegment {
  id: number
  text: string
  start: number
  end: number
  speaker?: string
  /** PR-Translate.1: 청크 번역 완료 시 채워짐. 미번역 segment는 undefined. */
  translation_ko?: string
}

export interface DocumentData {
  page_count: number
  text_content?: string
  ocr_text?: string
  html_content?: string
}

export function getSpeakerCount(
  content: Pick<ContentSummary, 'speakers' | 'transcription'>
): number {
  return content.transcription?.speakers?.length ?? content.speakers?.length ?? 0
}

export function getDurationSeconds(
  content: Pick<ContentSummary, 'duration_seconds' | 'transcription'>
): number | undefined {
  return content.transcription?.duration_seconds ?? content.duration_seconds
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
