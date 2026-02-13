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
import { usePrefetchOnView } from '@/shared/hooks/usePrefetchOnView'
import {
  type ContentSummary,
  type ContentStatus,
  STATUS_LABELS,
  getStatusVariant,
  getFileExtension,
} from '../types'
import { useDisplayProgress } from '../hooks/useDisplayProgress'

const PROCESSING_STATUSES: ContentStatus[] = [
  'QUEUED', 'PULLING', 'PROCESSING', 'OCR_PROCESSING', 'SUMMARY_QUEUED', 'SUMMARIZING',
]

const FAILED_STATUSES: ContentStatus[] = [
  'DOWNLOAD_FAILED', 'ASR_FAILED', 'OCR_FAILED', 'SUMMARY_FAILED',
]

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
  const iconClass = "h-4 w-4"
  switch (type) {
    case 'DOCUMENT':
      return <FileText className={iconClass} />
    case 'PORTRAY':
      return <ImageIcon className={iconClass} />
    case 'AUDIO':
      return <Music className={iconClass} />
    default:
      return null
  }
}

function getContentTypeStyle(_type: string) {
  // 심플한 단일 스타일
  return 'bg-muted/50 text-muted-foreground'
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
  selectionMode?: boolean
  onToggle?: (id: string) => void
  onRetry?: (id: string, type: 'download' | 'asr' | 'ocr' | 'summary') => void
}

export function ContentCard({ content, selected, selectionMode, onToggle, onRetry }: ContentCardProps) {
  const status = content.status
  const isProcessing = PROCESSING_STATUSES.includes(status)
  const isFailed = FAILED_STATUSES.includes(status)
  const displayProgress = useDisplayProgress(content)

  // 뷰포트 진입 시 상세 데이터 prefetch (COMPLETED 상태만)
  const prefetchRef = usePrefetchOnView({
    contentId: content.id,
    delay: 300,
    enabled: status === 'COMPLETED',
  })

  const isSelected = Boolean(selectionMode && selected)
  const metaText =
    content.content_type === 'AUDIO' && status === 'COMPLETED'
      ? `화자 ${content.speakers?.length || 0}명 · ${formatDuration(content.duration_seconds || 0)}`
      : (content.content_type === 'DOCUMENT' || content.content_type === 'PORTRAY') &&
          status === 'COMPLETED' &&
          content.document?.page_count
        ? `${content.document.page_count}페이지`
        : null
  const body = (
    <div className="flex-1 min-h-[2.75rem] mb-3">
      <h3 className="text-base font-medium leading-snug line-clamp-2">
        {content.title || content.filename}
      </h3>
    </div>
  )
  const footer = (
    <div className="space-y-2 mt-auto pt-2 border-t border-border/50">
      {isProcessing ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2.5">
            <Badge variant={getStatusVariant(status)} className="text-xs flex items-center gap-1.5 shrink-0">
              {getStatusIcon(status)}
              {STATUS_LABELS[status]}
            </Badge>
            <Progress value={displayProgress} className="h-1.5 flex-1" />
            <span className="text-xs text-muted-foreground shrink-0">
              {displayProgress}%
            </span>
          </div>
          <span className="text-xs text-muted-foreground">
            {formatDate(content.created_at)}
          </span>
        </div>
      ) : (
        <div className="flex items-center justify-between gap-2">
          <Badge variant={getStatusVariant(status)} className="text-xs flex items-center gap-1.5">
            {getStatusIcon(status)}
            {STATUS_LABELS[status]}
          </Badge>
          <span className="text-xs text-muted-foreground shrink-0">
            {formatDate(content.created_at)}
          </span>
        </div>
      )}
    </div>
  )

  return (
    <Card
      ref={prefetchRef}
      className={`hover:shadow-md transition-shadow h-full flex flex-col ${isSelected ? 'bg-muted/30' : ''}`}
      onClick={selectionMode && onToggle ? () => onToggle(content.id) : undefined}
    >
      <div className="flex flex-col flex-1 px-5 py-4">
        {/* Header: 타입 아이콘 + 확장자 + 체크박스 */}
        <div className="flex items-center gap-2 mb-3">
          {selectionMode ? (
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <div className={`flex items-center justify-center w-7 h-7 rounded flex-shrink-0 ${getContentTypeStyle(content.content_type)}`}>
                {getContentTypeIcon(content.content_type)}
              </div>
              {getFileExtension(content.filename) && (
                <Badge variant="outline" className="text-xs">
                  {getFileExtension(content.filename)}
                </Badge>
              )}
            </div>
          ) : (
            <Link
              to={`/contents/${content.id}`}
              className="flex items-center gap-2 flex-1 min-w-0"
            >
              <div className={`flex items-center justify-center w-7 h-7 rounded flex-shrink-0 ${getContentTypeStyle(content.content_type)}`}>
                {getContentTypeIcon(content.content_type)}
              </div>
              {getFileExtension(content.filename) && (
                <Badge variant="outline" className="text-xs">
                  {getFileExtension(content.filename)}
                </Badge>
              )}
            </Link>
          )}
          <div className="flex items-center text-xs text-muted-foreground">
            {metaText && <span>{metaText}</span>}
          </div>
          {selectionMode && onToggle && (
            <div
              className="flex-shrink-0"
              onClick={(event) => event.stopPropagation()}
            >
              <Checkbox
                checked={selected}
                onCheckedChange={() => onToggle(content.id)}
              />
            </div>
          )}
        </div>

        {selectionMode ? (
          <div className="flex flex-col flex-1">
            {body}
            {footer}
          </div>
        ) : (
          <Link to={`/contents/${content.id}`} className="flex flex-col flex-1">
            {body}
            {footer}
          </Link>
        )}
      </div>

      {/* 실패 시 재처리 버튼 */}
      {onRetry && isFailed && (
        <div className="px-5 pb-4">
          <Button
            type="button"
            variant={status === 'SUMMARY_FAILED' ? 'secondary' : 'default'}
            onClick={() =>
              onRetry(
                content.id,
                status === 'DOWNLOAD_FAILED' ? 'download'
                  : status === 'ASR_FAILED' ? 'asr'
                  : status === 'OCR_FAILED' ? 'ocr'
                  : 'summary'
              )
            }
            className="w-full h-9 text-sm"
          >
            {status === 'DOWNLOAD_FAILED'
              ? '다운로드 재시도'
              : status === 'ASR_FAILED'
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
