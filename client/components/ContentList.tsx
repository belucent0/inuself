'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ContentSummary, ContentStatus, deleteContentsBulk, retryProcessing } from '@/lib/api'
import { formatToKST } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { cn } from '@/lib/utils'

type PaginationProps = {
  currentPage: number
  totalPages: number
  total: number
  pageSize: number
  onPageChange: (page: number) => void
}

type Props = {
  contents: ContentSummary[]
  pagination?: PaginationProps
  onRefresh?: () => void
}

const statusLabels: Record<ContentStatus, string> = {
  QUEUED: '대기중',
  PROCESSING: '처리중',
  SUMMARIZING: '요약중',
  COMPLETED: '완료',
  ASR_FAILED: 'ASR 실패',
  SUMMARY_FAILED: '요약 실패',
  CANCELLED: '취소됨',
}

const getStatusVariant = (status: ContentStatus): 'default' | 'secondary' | 'destructive' | 'outline' => {
  switch (status) {
    case 'COMPLETED':
      return 'default'
    case 'ASR_FAILED':
    case 'SUMMARY_FAILED':
      return 'destructive'
    case 'PROCESSING':
    case 'SUMMARIZING':
      return 'secondary'
    default:
      return 'outline'
  }
}

export default function ContentList({ contents, pagination, onRefresh }: Props) {
  const router = useRouter()
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [isDeleting, setIsDeleting] = useState(false)
  const [message, setMessage] = useState<string>('')

  const selectableIds = useMemo(() => contents.map((content) => content.id), [contents])

  useEffect(() => {
    setSelectedIds((prev) => {
      if (!prev.size) {
        return prev
      }
      const next = new Set<number>()
      selectableIds.forEach((id) => {
        if (prev.has(id)) {
          next.add(id)
        }
      })
      return next
    })
  }, [selectableIds])

  const toggleSelection = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const handleSelectAll = () => {
    setSelectedIds((prev) => {
      if (!selectableIds.length) {
        return new Set()
      }
      const isAllSelected = selectableIds.every((id) => prev.has(id))
      return isAllSelected ? new Set() : new Set(selectableIds)
    })
  }

  const handleBulkDelete = async () => {
    if (!selectedIds.size) {
      return
    }

    if (!confirm('선택한 대기중 콘텐츠를 삭제하시겠습니까?')) {
      return
    }

    setIsDeleting(true)
    setMessage('')

    try {
      const result = await deleteContentsBulk(Array.from(selectedIds))
      setMessage(result.message)
      setSelectedIds(new Set())
      if (onRefresh) {
        onRefresh()
      } else {
        router.refresh()
      }
      setTimeout(() => setMessage(''), 3000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '삭제 실패')
    } finally {
      setIsDeleting(false)
    }
  }

  const handleRetry = async (contentId: number, type: 'asr' | 'summary', event: React.MouseEvent) => {
    event.stopPropagation()
    const typeLabel = type === 'asr' ? 'ASR 처리' : 'LLM 요약'
    if (!confirm(`${typeLabel}를 다시 시도하시겠습니까?`)) {
      return
    }
    
    try {
      const result = await retryProcessing(contentId, type)
      setMessage(result.message)
      if (onRefresh) {
        onRefresh()
      } else {
        router.refresh()
      }
      setTimeout(() => setMessage(''), 3000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '재처리 실패')
    }
  }

  if (!contents.length) {
    return (
      <Card>
        <CardContent className="pt-6">
          <p className="text-muted-foreground">아직 처리된 콘텐츠가 없습니다. 파일을 업로드해 보세요.</p>
        </CardContent>
      </Card>
    )
  }

  const allSelected =
    selectableIds.length > 0 && selectableIds.every((id) => selectedIds.has(id))

  return (
    <div className="space-y-4">
      <div className="flex gap-2 flex-wrap">
        <Button
          type="button"
          variant={allSelected ? 'secondary' : 'outline'}
          onClick={handleSelectAll}
          disabled={!selectableIds.length}
        >
          {allSelected ? '선택 해제' : '전체 선택'}
        </Button>
        <Button
          type="button"
          variant="destructive"
          onClick={handleBulkDelete}
          disabled={isDeleting || selectedIds.size === 0}
        >
          {isDeleting ? '삭제 중...' : `선택 삭제 (${selectedIds.size}개)`}
        </Button>
      </div>
      
      {message && (
        <div className={cn(
          "p-3 rounded-md text-sm",
          message.includes('실패') ? "bg-destructive/10 text-destructive" : "bg-primary/10 text-primary"
        )}>
          {message}
        </div>
      )}
      
      <div className="space-y-4">
        {contents.map((item) => (
          <Card key={item.id} className="hover:shadow-md transition-shadow">
            <CardHeader className="pb-3">
              <div className="flex items-start gap-3">
                <Checkbox
                  checked={selectedIds.has(item.id)}
                  onCheckedChange={() => toggleSelection(item.id)}
                  onClick={(e) => e.stopPropagation()}
                  className="mt-1"
                />
                <div className="flex-1 min-w-0">
                  <Link href={`/contents/${item.id}`} className="block">
                    <CardTitle className="text-lg mb-2 break-words">
                      {item.title || item.filename}
                    </CardTitle>
                    <div className="flex items-center gap-2 flex-wrap">
                      <Badge variant={getStatusVariant(item.status)}>
                        {statusLabels[item.status]}
                      </Badge>
                      <span className="text-sm text-muted-foreground">
                        화자 수: {item.speakers.length || 0} · 재생 길이: {item.duration_seconds.toFixed(1)}초
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground mt-2">
                      {formatToKST(item.created_at)}
                    </p>
                  </Link>
                </div>
              </div>
            </CardHeader>
            {(item.status === 'ASR_FAILED' || item.status === 'SUMMARY_FAILED') && (
              <CardContent className="pt-0">
                <Button
                  type="button"
                  variant={item.status === 'ASR_FAILED' ? 'default' : 'secondary'}
                  onClick={(e) => handleRetry(item.id, item.status === 'ASR_FAILED' ? 'asr' : 'summary', e)}
                  className="w-full"
                >
                  {item.status === 'ASR_FAILED' ? 'ASR 재처리' : '요약 재처리'}
                </Button>
              </CardContent>
            )}
          </Card>
        ))}
      </div>
      
      {pagination && pagination.totalPages > 1 && (
        <div className="flex flex-col items-center gap-4 mt-8">
          <div className="flex items-center gap-2 flex-wrap justify-center">
            <Button
              type="button"
              variant="outline"
              onClick={() => pagination.onPageChange(pagination.currentPage - 1)}
              disabled={pagination.currentPage === 1}
            >
              이전
            </Button>
            <div className="flex items-center gap-1">
              {Array.from({ length: pagination.totalPages }, (_, i) => i + 1)
                .filter((pageNum) => {
                  const diff = Math.abs(pageNum - pagination.currentPage)
                  return diff <= 2 || pageNum === 1 || pageNum === pagination.totalPages
                })
                .map((pageNum, index, array) => {
                  const showEllipsis = index > 0 && pageNum - array[index - 1] > 1
                  return (
                    <div key={pageNum} className="flex items-center gap-1">
                      {showEllipsis && (
                        <span className="px-2 text-muted-foreground">...</span>
                      )}
                      <Button
                        type="button"
                        variant={pageNum === pagination.currentPage ? 'default' : 'outline'}
                        size="icon"
                        onClick={() => pagination.onPageChange(pageNum)}
                        className="min-w-[44px]"
                      >
                        {pageNum}
                      </Button>
                    </div>
                  )
                })}
            </div>
            <Button
              type="button"
              variant="outline"
              onClick={() => pagination.onPageChange(pagination.currentPage + 1)}
              disabled={pagination.currentPage === pagination.totalPages}
            >
              다음
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">
            전체 {pagination.total}개 중 {((pagination.currentPage - 1) * pagination.pageSize) + 1}-
            {Math.min(pagination.currentPage * pagination.pageSize, pagination.total)}개 표시
          </p>
        </div>
      )}
    </div>
  )
}
