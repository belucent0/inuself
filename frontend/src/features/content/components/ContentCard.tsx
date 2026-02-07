/**
 * 콘텐츠 카드 컴포넌트
 * - 상단: 확장자 + 날짜 (공통)
 * - 중앙: 타입 아이콘 + 제목
 * - 하단: 상태 배지 + 타입별 메타 OR 프로그레스
 */

import { Link } from 'react-router-dom'
import { FileText, Music, Image as ImageIcon, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { Card } from '@/shared/components/ui/card'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Progress } from '@/shared/components/ui/progress'
import {
  type ContentSummary,
  type ContentStatus,
  STATUS_LABELS,
  getStatusVariant,
  getFileExtension,
} from '../types'

const PROCESSING_STATUSES: ContentStatus[] = [
  'QUEUED', 'PULLING', 'PROCESSING', 'OCR_PROCESSING', 'SUMMARY_QUEUED', 'SUMMARIZING',
]

const FAILED_STATUSES: ContentStatus[] = [
  'ASR_FAILED', 'OCR_FAILED', 'SUMMARY_FAILED',
]

const PROGRESS_MAP: Partial<Record<ContentStatus, number>> = {
  QUEUED: 10,
  PULLING: 25,
  PROCESSING: 50,
  OCR_PROCESSING: 50,
  SUMMARY_QUEUED: 70,
  SUMMARIZING: 85,
}

function getStatusIcon(status: ContentStatus) {
  if (PROCESSING_STATUSES.includes(status)) {
    return <Loader2 className="size-3.5 animate-spin" />
  }
  if (status === 'COMPLETED') {
    return <CheckCircle2 className="size-3.5" />
  }
  if (FAILED_STATUSES.includes(status) || status === 'CANCELLED') {
    return <XCircle className="size-3.5" />
  }
  return null
}

function getContentTypeIcon(type: string) {
  switch (type) {
    case 'DOCUMENT':
      return <FileText className="h-5 w-5 text-muted-foreground flex-shrink-0" />
    case 'PORTRAY':
      return <ImageIcon className="h-5 w-5 text-muted-foreground flex-shrink-0" />
    case 'AUDIO':
      return <Music className="h-5 w-5 text-muted-foreground flex-shrink-0" />
    default:
      return null
  }
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDuration(seconds: number): string {
  const hrs = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)
  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

interface ContentCardProps {
  content: ContentSummary
  selected?: boolean
  onToggle?: (id: string) => void
  onRetry?: (id: string, type: 'asr' | 'ocr' | 'summary') => void
}

export function ContentCard({ content, selected, onToggle, onRetry }: ContentCardProps) {
  const status = content.status
  const isProcessing = PROCESSING_STATUSES.includes(status)
  const isFailed = FAILED_STATUSES.includes(status)

  return (
    <Card className="hover:shadow-md transition-shadow h-full flex flex-col relative">
      {onToggle && (
        <Checkbox
          checked={selected}
          onCheckedChange={() => onToggle(content.id)}
          onClick={(e) => e.stopPropagation()}
          className="absolute top-4 right-4 z-10"
        />
      )}

      <Link to={`/contents/${content.id}`} className="flex flex-col flex-1 px-5 py-4 gap-3">
        {/* 상단: 확장자 + 날짜 */}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {getFileExtension(content.filename) && (
            <Badge variant="outline" className="text-xs">
              {getFileExtension(content.filename)}
            </Badge>
          )}
          <span>{formatDate(content.created_at)}</span>
        </div>

        {/* 중앙: 타입 아이콘 + 제목 */}
        <div className="flex items-start gap-2.5 flex-1">
          {getContentTypeIcon(content.content_type)}
          <h3 className="text-base font-semibold leading-snug line-clamp-2 min-h-[2.5rem] pr-4">
            {content.title || content.filename}
          </h3>
        </div>

        {/* 하단: 상태 + 타입별 메타 */}
        <div className="space-y-2">
          {isProcessing ? (
            <div className="flex items-center gap-2.5">
              <Badge variant={getStatusVariant(status)} className="text-xs flex items-center gap-1.5 shrink-0">
                {getStatusIcon(status)}
                {STATUS_LABELS[status]}
              </Badge>
              <Progress value={PROGRESS_MAP[status] || 0} className="h-1.5 flex-1" />
              <span className="text-xs text-muted-foreground shrink-0">
                {PROGRESS_MAP[status] || 0}%
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant={getStatusVariant(status)} className="text-xs flex items-center gap-1.5">
                {getStatusIcon(status)}
                {STATUS_LABELS[status]}
              </Badge>
              {content.content_type === 'AUDIO' && status === 'COMPLETED' && (
                <span className="text-sm text-muted-foreground">
                  화자 {content.speakers?.length || 0}명 · {formatDuration(content.duration_seconds || 0)}
                </span>
              )}
              {(content.content_type === 'DOCUMENT' || content.content_type === 'PORTRAY') &&
                status === 'COMPLETED' &&
                content.document?.page_count && (
                  <span className="text-sm text-muted-foreground">
                    {content.document.page_count}페이지
                  </span>
                )}
            </div>
          )}
        </div>
      </Link>

      {/* 실패 시 재처리 버튼 */}
      {onRetry && isFailed && (
        <div className="px-5 pb-4">
          <Button
            type="button"
            variant={status === 'SUMMARY_FAILED' ? 'secondary' : 'default'}
            onClick={() =>
              onRetry(
                content.id,
                status === 'ASR_FAILED' ? 'asr' : status === 'OCR_FAILED' ? 'ocr' : 'summary'
              )
            }
            className="w-full h-9 text-sm"
          >
            {status === 'ASR_FAILED'
              ? 'ASR 재처리'
              : status === 'OCR_FAILED'
              ? 'OCR 재처리'
              : '요약 재처리'}
          </Button>
        </div>
      )}
    </Card>
  )
}
