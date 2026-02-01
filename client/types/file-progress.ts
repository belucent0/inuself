/** 파일 처리 상태 (WebSocket) */
export type FileStatus =
    | 'queued'
    | 'processing'
    | 'summary_queued'
    | 'summarizing'
    | 'completed'
    | 'failed'

/** 파일 처리 단계 (WebSocket) */
export type FileProgressStep =
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

/** 파일 진행 상태 이벤트 */
export interface FileProgressEvent {
    type: 'file_progress' | 'connection' | 'pong' | 'content_created'
    file_id?: string  // UUID
    content_id?: string  // UUID
    filename?: string
    content_type?: string
    status?: FileStatus | string
    step?: FileProgressStep
    progress?: number
    message?: string
    timestamp?: string
    channel?: string
    metadata?: {
        title?: string
        duration_seconds?: number
        speakers?: string[]
        page_count?: number
    }
}

/** 파일 진행 상태 */
export interface FileProgress {
    /** 파일 ID (UUID) */
    fileId: string | null
    /** 현재 상태 */
    status: FileStatus
    /** 현재 처리 단계 */
    step: FileProgressStep | null
    /** 진행률 (0-100) */
    progress: number
    /** 상태 메시지 */
    message: string
    /** 마지막 업데이트 시간 */
    lastUpdate: Date | null
    /** WebSocket 연결 상태 */
    isConnected: boolean
}
