/**
 * Sonner 커스텀 토스트: 파일 다운로드 진행 상태
 *
 * SSE 이벤트를 구독하여 실시간으로 진행 상황을 업데이트합니다.
 */

import { useEffect, useState } from 'react'
import { Loader2, CheckCircle2, XCircle, AlertCircle } from 'lucide-react'
import { toast } from 'sonner'
import { useFileProgressSSE } from '@/shared/hooks/useFileProgressSSE'
import type { FileProgressEvent } from '@/features/upload/types'

const STEP_LABELS: Record<string, string> = {
  youtube_download: 'YouTube 다운로드',
  uploading: '업로드',
  asr: 'ASR 처리',
  ocr: 'OCR 처리',
  llm: 'LLM 요약',
  completed: '완료',
  error: '오류',
}

interface DownloadProgressToastProps {
  fileId: string
  toastId: string | number
}

export function DownloadProgressToast({ fileId, toastId }: DownloadProgressToastProps) {
  const [event, setEvent] = useState<FileProgressEvent | null>(null)
  const { addListener, removeListener } = useFileProgressSSE()

  useEffect(() => {
    const handleProgress = (progressEvent: FileProgressEvent) => {
      // 이 토스트가 모니터링하는 파일의 이벤트인지 확인
      if (progressEvent.file_id !== fileId && progressEvent.content_id !== fileId) {
        return
      }

      setEvent(progressEvent)

      // 완료 상태로 전환되면 토스트를 업데이트
      if (progressEvent.status === 'COMPLETED') {
        toast.success('다운로드 완료됨', { id: String(toastId) })
      }

      // 실패 상태로 전환되면 토스트를 업데이트
      if (progressEvent.status === 'FAILED' || progressEvent.status === 'DOWNLOAD_FAILED') {
        toast.error('다운로드 실패', { id: String(toastId) })
      }
    }

    addListener(handleProgress)
    return () => removeListener(handleProgress)
  }, [fileId, toastId, addListener, removeListener])

  if (!event) {
    return (
      <div className="flex items-center gap-3 w-full">
        <Loader2 className="h-5 w-5 animate-spin flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">다운로드 준비 중...</p>
        </div>
      </div>
    )
  }

  const getStatusIcon = () => {
    switch (event.status) {
      case 'COMPLETED':
        return <CheckCircle2 className="h-5 w-5 text-green-500 flex-shrink-0" />
      case 'FAILED':
      case 'DOWNLOAD_FAILED':
        return <XCircle className="h-5 w-5 text-red-500 flex-shrink-0" />
      default:
        return <Loader2 className="h-5 w-5 animate-spin flex-shrink-0" />
    }
  }

  const getStepLabel = () => {
    if (event.step) {
      return STEP_LABELS[event.step] || event.step
    }
    return event.message || '처리 중...'
  }

  const progress = event.progress ?? 0

  return (
    <div className="flex flex-col gap-2 w-full max-w-sm">
      <div className="flex items-center gap-3">
        {getStatusIcon()}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate">{getStepLabel()}</p>
          {event.metadata?.title && (
            <p className="text-xs text-muted-foreground truncate">{event.metadata.title}</p>
          )}
        </div>
        <span className="text-sm font-medium flex-shrink-0">{Math.round(progress)}%</span>
      </div>

      {/* 프로그레스 바 */}
      <div className="w-full bg-muted rounded-full h-2 overflow-hidden">
        <div
          className="bg-primary h-full transition-all duration-300"
          style={{ width: `${Math.min(progress, 100)}%` }}
        />
      </div>

      {/* 상태 메시지 */}
      {event.message && (
        <p className="text-xs text-muted-foreground">{event.message}</p>
      )}
    </div>
  )
}
