/**
 * 콘텐츠 상세 헤더
 * - 제목, 상태 배지, 메타 정보
 * - 다운로드, 재시도, 삭제 액션
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft,
  Download,
  RotateCcw,
  Trash2,
  FileText,
  Music,
  Image as ImageIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/shared/components/ui/button'
import { Badge } from '@/shared/components/ui/badge'
import { DeleteConfirmDialog } from '@/shared/components/DeleteConfirmDialog'
import { cn } from '@/shared/utils/cn'
import type { ContentDetail } from '../types'
import { STATUS_LABELS, getStatusVariant, getFileExtension } from '../types'

interface ContentHeaderProps {
  content: ContentDetail
  onDelete?: () => Promise<void>
  onRetryClick?: (type: 'asr' | 'ocr' | 'summary') => void
}

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
}

const CONTENT_TYPE_ICONS = {
  DOCUMENT: FileText,
  PORTRAY: ImageIcon,
  AUDIO: Music,
} as const

export function ContentHeader({ content, onDelete, onRetryClick }: ContentHeaderProps) {
  const navigate = useNavigate()
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const handleDeleteConfirm = async () => {
    if (!onDelete) return

    setIsDeleting(true)
    try {
      await onDelete()
      toast.success(`"${content.filename}" 삭제 완료`)
      navigate('/contents')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '삭제에 실패했습니다.')
    } finally {
      setIsDeleting(false)
      setDeleteDialogOpen(false)
    }
  }

  const isFailedStatus = ['ASR_FAILED', 'OCR_FAILED', 'SUMMARY_FAILED'].includes(content.status)
  const retryType = content.status === 'ASR_FAILED'
    ? 'asr'
    : content.status === 'OCR_FAILED'
      ? 'ocr'
      : 'summary'

  const Icon = CONTENT_TYPE_ICONS[content.content_type] || FileText

  const metaParts: string[] = []
  if (content.content_type === 'AUDIO' && content.transcription) {
    metaParts.push(`${content.transcription.speakers?.length || 0}명 화자`)
    if (content.transcription.duration_seconds) {
      const mins = Math.floor(content.transcription.duration_seconds / 60)
      metaParts.push(`${mins}분`)
    }
  }
  if (content.content_type === 'DOCUMENT' && content.document) {
    metaParts.push(`${content.document.page_count}페이지`)
  }

  return (
    <div className="flex flex-col gap-2 px-4 py-3 border-b bg-background shrink-0">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0"
          onClick={() => navigate('/contents')}
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>

        <h1 className="text-lg font-semibold truncate flex-1">
          {content.title || content.filename}
        </h1>

        <div className="flex items-center gap-1 shrink-0">
          {content.file_url && (
            <Button variant="ghost" size="icon" className="h-8 w-8" asChild>
              <a href={content.file_url} download title="다운로드">
                <Download className="h-4 w-4" />
              </a>
            </Button>
          )}
          {isFailedStatus && onRetryClick && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => onRetryClick(retryType)}
              title="재시도"
            >
              <RotateCcw className="h-4 w-4" />
            </Button>
          )}
          {onDelete && (
            <Button
              variant="ghost"
              size="icon"
              className={cn('h-8 w-8', 'text-destructive hover:text-destructive')}
              onClick={() => setDeleteDialogOpen(true)}
              disabled={isDeleting}
              title="삭제"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 pl-11 flex-wrap">
        <Badge variant={getStatusVariant(content.status)} className="text-xs">
          {STATUS_LABELS[content.status]}
        </Badge>
        <Badge variant="outline" className="text-xs gap-1">
          <Icon className="h-3 w-3" />
          {getFileExtension(content.filename)}
        </Badge>
        <span className="text-xs text-muted-foreground">
          {formatDate(content.created_at)}
        </span>
        {metaParts.map((part, i) => (
          <span key={i} className="text-xs text-muted-foreground">
            · {part}
          </span>
        ))}
      </div>

      <DeleteConfirmDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleDeleteConfirm}
        description={`"${content.filename}"을(를) 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`}
        isDeleting={isDeleting}
      />
    </div>
  )
}
