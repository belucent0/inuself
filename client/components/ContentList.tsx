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
import { ChevronsLeft, ChevronsRight, ChevronLeft, ChevronRight } from 'lucide-react'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

type PaginationProps = {
  currentPage: number
  totalPages: number
  total: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange?: (pageSize: number) => void
}

type Props = {
  contents: ContentSummary[]
  pagination?: PaginationProps
  onRefresh?: () => void
}

const statusLabels: Record<ContentStatus, string> = {
  QUEUED: '대기중',
  PROCESSING: '인식중',
  SUMMARIZING: '요약중',
  COMPLETED: '완료',
  ASR_FAILED: 'ASR 실패',
  SUMMARY_FAILED: '요약 실패',
  CANCELLED: '취소됨',
}

const getStatusVariant = (status: ContentStatus): 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info' => {
  switch (status) {
    case 'COMPLETED':
      return 'success'
    case 'ASR_FAILED':
      return 'destructive'
    case 'SUMMARY_FAILED':
      return 'warning'
    case 'PROCESSING':
    case 'SUMMARIZING':
      return 'info'
    case 'QUEUED':
    case 'CANCELLED':
      return 'outline'
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
    <div className="space-y-2 md:space-y-4 pt-2 md:pt-0">
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
      
      <div className="space-y-2.5 md:space-y-4">
        {contents.map((item) => (
          <Card key={item.id} className="hover:shadow-md transition-shadow">
            <CardHeader className="pb-2.5 md:pb-3 px-4 md:px-6 pt-4 md:pt-6">
              <div className="flex items-start gap-2.5 md:gap-3">
                <Checkbox
                  checked={selectedIds.has(item.id)}
                  onCheckedChange={() => toggleSelection(item.id)}
                  onClick={(e) => e.stopPropagation()}
                  className="mt-0.5 md:mt-1"
                />
                <div className="flex-1 min-w-0">
                  <Link href={`/contents/${item.id}`} className="block">
                    <CardTitle className="text-[15px] md:text-lg mb-1.5 md:mb-2 break-words leading-snug">
                      {item.title || item.filename}
                    </CardTitle>
                    <div className="flex items-center gap-1.5 md:gap-2 flex-wrap">
                      <Badge variant={getStatusVariant(item.status)} className="text-xs">
                        {statusLabels[item.status]}
                      </Badge>
                      <span className="text-[13px] md:text-sm text-muted-foreground">
                        화자 수: {item.speakers.length || 0} · 재생 길이: {item.duration_seconds.toFixed(1)}초
                      </span>
                    </div>
                    <p className="text-[11px] md:text-xs text-muted-foreground mt-1.5 md:mt-2">
                      {formatToKST(item.created_at)}
                    </p>
                  </Link>
                </div>
              </div>
            </CardHeader>
            {(item.status === 'ASR_FAILED' || item.status === 'SUMMARY_FAILED') && (
              <CardContent className="pt-0 px-4 md:px-6 pb-3 md:pb-6">
                <Button
                  type="button"
                  variant={item.status === 'ASR_FAILED' ? 'default' : 'secondary'}
                  onClick={(e) => handleRetry(item.id, item.status === 'ASR_FAILED' ? 'asr' : 'summary', e)}
                  className="w-full h-8 md:h-10 text-xs md:text-sm"
                >
                  {item.status === 'ASR_FAILED' ? 'ASR 재처리' : '요약 재처리'}
                </Button>
              </CardContent>
            )}
          </Card>
        ))}
      </div>
      
      {pagination && (
        <div className="relative flex items-center justify-between px-2 py-4">
          {/* 왼쪽: 행 수 */}
          <div className="flex items-center gap-2">
            <Select 
              value={pagination.pageSize.toString()} 
              onValueChange={(value) => {
                if (pagination.onPageSizeChange) {
                  pagination.onPageSizeChange(parseInt(value, 10))
                }
              }}
            >
              <SelectTrigger className="h-8 w-[70px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="5">5</SelectItem>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
                <SelectItem value="100">100</SelectItem>
              </SelectContent>
            </Select>
              <span className="hidden md:inline text-sm text-muted-foreground">개 행</span>
          </div>

          {/* 중앙: 네비게이션 버튼들 (정중앙) */}
          <div className="absolute left-1/2 transform -translate-x-1/2 flex items-center gap-2">
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => pagination.onPageChange(1)}
              disabled={pagination.currentPage === 1}
              title="Go to first page"
            >
              <ChevronsLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => pagination.onPageChange(pagination.currentPage - 1)}
              disabled={pagination.currentPage === 1}
              title="Go to previous page"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => pagination.onPageChange(pagination.currentPage + 1)}
              disabled={pagination.currentPage === pagination.totalPages}
              title="Go to next page"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => pagination.onPageChange(pagination.totalPages)}
              disabled={pagination.currentPage === pagination.totalPages}
              title="Go to last page"
            >
              <ChevronsRight className="h-4 w-4" />
            </Button>
          </div>

          {/* 오른쪽: 선택된 항목 수 - 데스크톱만 표시 */}
          <div className="hidden md:block text-sm text-muted-foreground">
            {selectedIds.size} / {pagination.total} 행 선택됨
          </div>
          {/* 모바일: 빈 공간 (중앙 정렬을 위해) */}
          <div className="md:hidden w-[70px]"></div>
        </div>
      )}
    </div>
  )
}
