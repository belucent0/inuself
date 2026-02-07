/**
 * 콘텐츠 상세 뷰 컴포넌트
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Download, Trash2, FileText, Music, Image as ImageIcon } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Badge } from '@/shared/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/components/ui/card'
import { Separator } from '@/shared/components/ui/separator'
import { cn } from '@/shared/utils/cn'
import type { ContentDetail } from '../types'
import { STATUS_LABELS, getStatusVariant, getFileExtension } from '../types'
import { ContentRenderer } from './renderers'

interface ContentDetailViewProps {
  content: ContentDetail
  onDelete?: () => Promise<void>
  onRetry?: (type: 'asr' | 'ocr' | 'summary') => Promise<void>
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

export function ContentDetailView({ content, onDelete, onRetry }: ContentDetailViewProps) {
  const navigate = useNavigate()
  const [message, setMessage] = useState('')
  const [isDeleting, setIsDeleting] = useState(false)

  const handleDelete = async () => {
    if (!onDelete) return
    if (!confirm(`"${content.filename}"을(를) 삭제하시겠습니까?`)) return

    setIsDeleting(true)
    try {
      await onDelete()
      setMessage('삭제되었습니다. 목록 페이지로 이동합니다...')
      setTimeout(() => navigate('/contents'), 1000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '삭제 실패')
    } finally {
      setIsDeleting(false)
    }
  }

  const handleRetry = async (type: 'asr' | 'ocr' | 'summary') => {
    if (!onRetry) return
    if (type === 'summary' && !confirm('LLM 요약을 다시 시도하시겠습니까?')) return

    try {
      await onRetry(type)
      setMessage('재처리 요청 완료')
      setTimeout(() => setMessage(''), 3000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '재처리 실패')
    }
  }

  const isFailedStatus =
    content.status === 'ASR_FAILED' ||
    content.status === 'OCR_FAILED' ||
    content.status === 'SUMMARY_FAILED'

  return (
    <div className="space-y-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={() => navigate('/contents')}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          목록으로
        </Button>

        <div className="flex gap-2">
          {content.file_url && (
            <Button variant="outline" asChild>
              <a href={content.file_url} download>
                <Download className="h-4 w-4 mr-2" />
                다운로드
              </a>
            </Button>
          )}
          {onDelete && (
            <Button variant="destructive" onClick={handleDelete} disabled={isDeleting}>
              <Trash2 className="h-4 w-4 mr-2" />
              {isDeleting ? '삭제 중...' : '삭제'}
            </Button>
          )}
        </div>
      </div>

      {/* 메시지 */}
      {message && (
        <div
          className={cn(
            'p-3 rounded-md text-sm',
            message.includes('실패')
              ? 'bg-destructive/10 text-destructive'
              : 'bg-primary/10 text-primary'
          )}
        >
          {message}
        </div>
      )}

      {/* 메타 정보 카드 */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            {content.content_type === 'DOCUMENT' ? (
              <FileText className="h-6 w-6 text-muted-foreground" />
            ) : content.content_type === 'PORTRAY' ? (
              <ImageIcon className="h-6 w-6 text-muted-foreground" />
            ) : (
              <Music className="h-6 w-6 text-muted-foreground" />
            )}
            <div className="flex-1">
              <CardTitle className="text-xl">{content.title || content.filename}</CardTitle>
              <p className="text-sm text-muted-foreground mt-1">{content.filename}</p>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-3 items-center">
            <Badge variant={getStatusVariant(content.status)}>
              {STATUS_LABELS[content.status]}
            </Badge>
            <Badge variant="outline">{getFileExtension(content.filename)}</Badge>
            <span className="text-sm text-muted-foreground">
              생성일: {formatDate(content.created_at)}
            </span>
            {content.content_type === 'AUDIO' && content.transcription && (
              <>
                <span className="text-sm text-muted-foreground">
                  화자: {content.transcription.speakers?.length || 0}명
                </span>
                <span className="text-sm text-muted-foreground">
                  재생시간: {content.transcription.duration_seconds?.toFixed(1)}초
                </span>
              </>
            )}
            {content.content_type === 'DOCUMENT' && content.document && (
              <span className="text-sm text-muted-foreground">
                페이지: {content.document.page_count}
              </span>
            )}
          </div>

          {/* 재시도 버튼 */}
          {isFailedStatus && onRetry && (
            <div className="mt-4">
              <Separator className="mb-4" />
              <Button
                variant={content.status === 'SUMMARY_FAILED' ? 'secondary' : 'default'}
                onClick={() =>
                  handleRetry(
                    content.status === 'ASR_FAILED'
                      ? 'asr'
                      : content.status === 'OCR_FAILED'
                      ? 'ocr'
                      : 'summary'
                  )
                }
              >
                {content.status === 'ASR_FAILED'
                  ? 'ASR 재처리'
                  : content.status === 'OCR_FAILED'
                  ? 'OCR 재처리'
                  : '요약 재처리'}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 콘텐츠 렌더러 */}
      {content.status === 'COMPLETED' && (
        <Card>
          <CardContent className="pt-6">
            <ContentRenderer content={content} />
          </CardContent>
        </Card>
      )}
    </div>
  )
}
