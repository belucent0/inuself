/**
 * 업로드 진행 상태 표시 컴포넌트
 */

import { Loader2, CheckCircle2, XCircle, AlertCircle } from 'lucide-react'
import { Progress } from '@/shared/components/ui/progress'
import { Card, CardContent } from '@/shared/components/ui/card'
import type { FileProgress, FileProgressStep } from '../types'

const STEP_LABELS: Record<FileProgressStep, string> = {
  youtube_download: 'YouTube 다운로드 중',
  uploading: '업로드 중',
  download_complete: '다운로드 완료',
  asr_pipeline_start: 'ASR 파이프라인 시작',
  asr_pipeline_complete: 'ASR 파이프라인 완료',
  asr_complete: 'ASR 완료',
  asr: 'ASR 처리 중',
  asr_completed: 'ASR 완료',
  asr_failed: 'ASR 실패',
  ocr: 'OCR 처리 중',
  ocr_completed: 'OCR 완료',
  ocr_failed: 'OCR 실패',
  llm: 'LLM 요약 중',
  llm_failed: 'LLM 요약 실패',
  summary_queued: '요약 대기 중',
  completed: '완료',
  error: '오류 발생',
}

interface UploadProgressProps {
  progress: FileProgress
}

export function UploadProgress({ progress }: UploadProgressProps) {
  const getStatusIcon = () => {
    switch (progress.status) {
      case 'QUEUED':
      case 'PULLING':
      case 'PROCESSING':
      case 'OCR_PROCESSING':
      case 'SUMMARY_QUEUED':
      case 'SUMMARIZING':
        return <Loader2 className="h-5 w-5 animate-spin text-primary" />
      case 'COMPLETED':
        return <CheckCircle2 className="h-5 w-5 text-green-500" />
      case 'FAILED':
      case 'DOWNLOAD_FAILED':
      case 'ASR_FAILED':
      case 'OCR_FAILED':
      case 'SUMMARY_FAILED':
        return <XCircle className="h-5 w-5 text-destructive" />
      default:
        return <AlertCircle className="h-5 w-5 text-muted-foreground" />
    }
  }

  const getStepLabel = () => {
    if (progress.step) {
      return STEP_LABELS[progress.step] || progress.step
    }
    return progress.message || '처리 중...'
  }

  if (!progress.fileId) {
    return null
  }

  return (
    <Card>
      <CardContent className="pt-4">
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            {getStatusIcon()}
            <div className="flex-1">
              <p className="text-sm font-medium">{getStepLabel()}</p>
              {progress.message && progress.step && (
                <p className="text-xs text-muted-foreground">{progress.message}</p>
              )}
            </div>
            <span className="text-sm font-medium">{progress.progress}%</span>
          </div>
          <Progress value={progress.progress} className="h-2" />
          {!progress.isConnected && (
            <p className="text-xs text-muted-foreground">
              연결 끊김 - 재연결 시도 중...
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
