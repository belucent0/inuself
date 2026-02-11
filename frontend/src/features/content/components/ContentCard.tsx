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
  onToggle?: (id: string) => void
  onRetry?: (id: string, type: 'download' | 'asr' | 'ocr' | 'summary') => void
}

export function ContentCard({ content, selected, onToggle, onRetry }: ContentCardProps) {
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

  return (
    <Card ref={prefetchRef} className="hover:shadow-md transition-shadow h-full flex flex-col">
      <Link to={`/contents/${content.id}`} className="flex flex-col flex-1 px-5 py-4">
        {/* Header: 타입 아이콘 + 확장자 + 체크박스 */}
        <div className="flex items-center gap-2 mb-3">
          <div className={`flex items-center justify-center w-7 h-7 rounded flex-shrink-0 ${getContentTypeStyle(content.content_type)}`}>
            {getContentTypeIcon(content.content_type)}
          </div>
          {getFileExtension(content.filename) && (
            <Badge variant="outline" className="text-xs">
              {getFileExtension(content.filename)}
            </Badge>
          )}
          <div className="flex-1" />
          {onToggle && (
            <Checkbox
              checked={selected}
              onCheckedChange={() => onToggle(content.id)}
              onClick={(e) => e.stopPropagation()}
              className="flex-shrink-0"
            />
          )}
        </div>

        {/* Body: 제목 (핵심 콘텐츠) */}
        <div className="flex-1 min-h-[2.75rem] mb-3">
          <h3 className="text-base font-medium leading-snug line-clamp-2">
            {content.title || content.filename}
          </h3>
        </div>

        {/* Footer: 상태 + 메타 + 날짜 */}
        <div className="space-y-2 mt-auto pt-2 border-t border-border/50">
          {isProcessing ? (
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
          ) : (
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Badge variant={getStatusVariant(status)} className="text-xs flex items-center gap-1.5">
                  {getStatusIcon(status)}
                  {STATUS_LABELS[status]}
                </Badge>
                {content.content_type === 'AUDIO' && status === 'COMPLETED' && (
                  <span className="text-xs text-muted-foreground">
                    화자 {content.speakers?.length || 0}명 · {formatDuration(content.duration_seconds || 0)}
                  </span>
                )}
                {(content.content_type === 'DOCUMENT' || content.content_type === 'PORTRAY') &&
                  status === 'COMPLETED' &&
                  content.document?.page_count && (
                    <span className="text-xs text-muted-foreground">
                      {content.document.page_count}페이지
                    </span>
                  )}
              </div>
              <span className="text-xs text-muted-foreground shrink-0">
                {formatDate(content.created_at)}
              </span>
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
