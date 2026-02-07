/**
 * 콘텐츠 카드 컴포넌트
 */

import { Link } from 'react-router-dom'
import { FileText, Music, Image as ImageIcon, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/shared/components/ui/card'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  type ContentSummary,
  type ContentStatus,
  STATUS_LABELS,
  getStatusVariant,
  getFileExtension,
} from '../types'

function getStatusIcon(status: ContentStatus) {
  switch (status) {
    case 'QUEUED':
    case 'PULLING':
    case 'PROCESSING':
    case 'OCR_PROCESSING':
    case 'SUMMARY_QUEUED':
    case 'SUMMARIZING':
      return <Loader2 className="size-3 animate-spin" />
    case 'COMPLETED':
      return <CheckCircle2 className="size-3" />
    case 'ASR_FAILED':
    case 'OCR_FAILED':
    case 'SUMMARY_FAILED':
    case 'CANCELLED':
      return <XCircle className="size-3" />
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

interface ContentCardProps {
  content: ContentSummary
  selected?: boolean
  onToggle?: (id: string) => void
  onRetry?: (id: string, type: 'asr' | 'ocr' | 'summary') => void
}

export function ContentCard({ content, selected, onToggle, onRetry }: ContentCardProps) {
  const status = content.status

  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardHeader className="pb-2.5 md:pb-3 px-4 md:px-6 pt-4 md:pt-6">
        <div className="flex items-start gap-2.5 md:gap-3">
          {onToggle && (
            <Checkbox
              checked={selected}
              onCheckedChange={() => onToggle(content.id)}
              onClick={(e) => e.stopPropagation()}
              className="mt-0.5 md:mt-1"
            />
          )}
          <div className="flex-1 min-w-0">
            <Link to={`/contents/${content.id}`} className="block">
              <div className="flex items-center gap-2 mb-1.5 md:mb-2">
                {content.content_type === 'DOCUMENT' ? (
                  <FileText className="h-4 w-4 md:h-5 md:w-5 text-muted-foreground flex-shrink-0" />
                ) : content.content_type === 'PORTRAY' ? (
                  <ImageIcon className="h-4 w-4 md:h-5 md:w-5 text-muted-foreground flex-shrink-0" />
                ) : content.content_type === 'AUDIO' ? (
                  <Music className="h-4 w-4 md:h-5 md:w-5 text-muted-foreground flex-shrink-0" />
                ) : null}
                <CardTitle className="text-[15px] md:text-lg break-words leading-snug">
                  {content.title || content.filename}
                </CardTitle>
              </div>
              <div className="flex items-center gap-1.5 md:gap-2 flex-wrap">
                <Badge variant={getStatusVariant(status)} className="text-xs flex items-center gap-1.5">
                  {getStatusIcon(status)}
                  {STATUS_LABELS[status]}
                </Badge>
                {getFileExtension(content.filename) && (
                  <Badge variant="outline" className="text-xs">
                    {getFileExtension(content.filename)}
                  </Badge>
                )}
                {content.content_type === 'AUDIO' && (
                  <span className="text-[13px] md:text-sm text-muted-foreground">
                    화자 수: {content.speakers?.length || 0} · 재생 길이: {content.duration_seconds?.toFixed(1)}초
                  </span>
                )}
              </div>
              <p className="text-[11px] md:text-xs text-muted-foreground mt-1.5 md:mt-2">
                {formatDate(content.created_at)}
              </p>
            </Link>
          </div>
        </div>
      </CardHeader>

      {onRetry && (status === 'ASR_FAILED' || status === 'OCR_FAILED' || status === 'SUMMARY_FAILED') && (
        <CardContent className="pt-0 px-4 md:px-6 pb-3 md:pb-6">
          <Button
            type="button"
            variant={status === 'SUMMARY_FAILED' ? 'secondary' : 'default'}
            onClick={() =>
              onRetry(
                content.id,
                status === 'ASR_FAILED' ? 'asr' : status === 'OCR_FAILED' ? 'ocr' : 'summary'
              )
            }
            className="w-full h-8 md:h-10 text-xs md:text-sm"
          >
            {status === 'ASR_FAILED'
              ? 'ASR 재처리'
              : status === 'OCR_FAILED'
              ? 'OCR 재처리'
              : '요약 재처리'}
          </Button>
        </CardContent>
      )}
    </Card>
  )
}
