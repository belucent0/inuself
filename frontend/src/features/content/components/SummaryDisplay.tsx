/**
 * 요약 표시 컴포넌트
 * - 상태별 분기: 대기/처리중/실패/완료
 * - MarkdownContent 재사용하여 summary 렌더링
 */

import { useState } from 'react'
import type { ContentDetail } from '../types'
import { MarkdownContent } from '@/features/chat/components/MarkdownContent'
import { Button } from '@/shared/components/ui/button'
import { contentsApi } from '@/shared/services/endpoints/contents'
import {
  Loader2,
  AlertCircle,
  Clock,
  RotateCcw,
  ImageIcon,
} from 'lucide-react'

interface SummaryDisplayProps {
  content: ContentDetail
  onRetryClick?: (type: 'asr' | 'ocr' | 'summary') => void
}

function SummaryStatusCard({
  status,
  onRetryClick,
}: {
  status: string
  onRetryClick?: (type: 'asr' | 'ocr' | 'summary') => void
}) {
  if (['QUEUED', 'PULLING', 'PROCESSING', 'OCR_PROCESSING'].includes(status)) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-8 w-8 animate-spin mb-3" />
        <p className="text-sm font-medium">콘텐츠 처리 중...</p>
        <p className="text-xs mt-1">처리가 완료되면 요약이 표시됩니다</p>
      </div>
    )
  }

  if (status === 'SUMMARY_QUEUED') {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Clock className="h-8 w-8 mb-3" />
        <p className="text-sm font-medium">요약 대기 중</p>
        <p className="text-xs mt-1">곧 요약이 시작됩니다</p>
      </div>
    )
  }

  if (status === 'SUMMARIZING') {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-8 w-8 animate-spin mb-3" />
        <p className="text-sm font-medium">요약 생성 중...</p>
      </div>
    )
  }

  if (['ASR_FAILED', 'OCR_FAILED', 'SUMMARY_FAILED'].includes(status)) {
    const failType = status === 'ASR_FAILED'
      ? 'asr'
      : status === 'OCR_FAILED'
        ? 'ocr'
        : 'summary'
    const failLabel = status === 'ASR_FAILED'
      ? '음성 인식'
      : status === 'OCR_FAILED'
        ? '문서 인식'
        : '요약 생성'

    return (
      <div className="flex flex-col items-center justify-center py-12">
        <AlertCircle className="h-8 w-8 text-destructive mb-3" />
        <p className="text-sm font-medium text-destructive">{failLabel} 실패</p>
        <p className="text-xs text-muted-foreground mt-1">
          재시도하여 다시 처리할 수 있습니다
        </p>
        {onRetryClick && (
          <Button
            variant="outline"
            size="sm"
            className="mt-4 gap-1.5"
            onClick={() => onRetryClick(failType as 'asr' | 'ocr' | 'summary')}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            재시도
          </Button>
        )}
      </div>
    )
  }

  return null
}

export function SummaryDisplay({
  content,
  onRetryClick,
}: SummaryDisplayProps) {
  const summaryText = content.summary_md || content.summary_html || content.summary
  const [coverImageUrl, setCoverImageUrl] = useState(content.cover_image_url)
  const [isRegenerating, setIsRegenerating] = useState(false)

  const handleRegenerateImage = async () => {
    setIsRegenerating(true)
    try {
      const result = await contentsApi.regenerateCoverImage(content.id)
      if (result.cover_image_url) {
        setCoverImageUrl(`${result.cover_image_url}?t=${Date.now()}`)
      }
    } catch {
      // 실패 시 조용히 무시 (toast 등 추가 가능)
    } finally {
      setIsRegenerating(false)
    }
  }

  if (content.status !== 'COMPLETED') {
    return (
      <SummaryStatusCard status={content.status} onRetryClick={onRetryClick} />
    )
  }

  return (
    <div className="space-y-6 max-w-3xl">
      {/* 커버 이미지 */}
      {coverImageUrl && (
        <div className="relative group">
          <div className="aspect-video w-full max-w-md overflow-hidden rounded-lg border">
            <img
              src={coverImageUrl}
              alt={content.title || '커버 이미지'}
              className="w-full h-full object-cover"
            />
          </div>
          <Button
            variant="outline"
            size="sm"
            className="absolute bottom-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity gap-1.5"
            onClick={handleRegenerateImage}
            disabled={isRegenerating}
          >
            {isRegenerating ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
            재생성
          </Button>
        </div>
      )}

      {/* 커버 이미지가 없을 때 생성 버튼 */}
      {!coverImageUrl && summaryText && (
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5"
          onClick={handleRegenerateImage}
          disabled={isRegenerating}
        >
          {isRegenerating ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <ImageIcon className="h-3.5 w-3.5" />
          )}
          커버 이미지 생성
        </Button>
      )}

      {summaryText ? (
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <MarkdownContent content={summaryText} />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">요약 내용이 없습니다.</p>
      )}
    </div>
  )
}
