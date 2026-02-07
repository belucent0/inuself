/**
 * 콘텐츠 목록 컴포넌트
 */

import { useState, useEffect, useMemo } from 'react'
import { ChevronsLeft, ChevronsRight, ChevronLeft, ChevronRight, Upload } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Card, CardContent } from '@/shared/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { cn } from '@/shared/utils/cn'
import type { ContentSummary } from '../types'
import { ContentCard } from './ContentCard'

interface PaginationProps {
  currentPage: number
  totalPages: number
  total: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange?: (pageSize: number) => void
}

interface ContentListProps {
  contents: ContentSummary[]
  pagination?: PaginationProps
  onDelete?: (ids: string[]) => Promise<void>
  onRetry?: (id: string, type: 'asr' | 'ocr' | 'summary') => Promise<void>
  onUpload?: () => void
}

export function ContentList({ contents, pagination, onDelete, onRetry, onUpload }: ContentListProps) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [isDeleting, setIsDeleting] = useState(false)
  const [message, setMessage] = useState('')

  const selectableIds = useMemo(() => contents.map((c) => c.id), [contents])

  useEffect(() => {
    setSelectedIds((prev) => {
      if (!prev.size) return prev
      const next = new Set<string>()
      selectableIds.forEach((id) => {
        if (prev.has(id)) next.add(id)
      })
      return next
    })
  }, [selectableIds])

  const toggleSelection = (id: string) => {
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
      if (!selectableIds.length) return new Set()
      const isAllSelected = selectableIds.every((id) => prev.has(id))
      return isAllSelected ? new Set() : new Set(selectableIds)
    })
  }

  const handleBulkDelete = async () => {
    if (!selectedIds.size || !onDelete) return
    if (!confirm('선택한 콘텐츠를 삭제하시겠습니까?')) return

    setIsDeleting(true)
    setMessage('')

    try {
      await onDelete(Array.from(selectedIds))
      setMessage('삭제되었습니다.')
      setSelectedIds(new Set())
      setTimeout(() => setMessage(''), 3000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '삭제 실패')
    } finally {
      setIsDeleting(false)
    }
  }

  const handleRetry = async (id: string, type: 'asr' | 'ocr' | 'summary') => {
    if (!onRetry) return
    try {
      await onRetry(id, type)
      setMessage('재처리 요청 완료')
      setTimeout(() => setMessage(''), 3000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '재처리 실패')
    }
  }

  if (!contents.length) {
    return (
      <Card>
        <CardContent className="pt-6">
          <p className="text-base text-muted-foreground">
            아직 처리된 콘텐츠가 없습니다. 파일을 업로드해 보세요.
          </p>
        </CardContent>
      </Card>
    )
  }

  const allSelected =
    selectableIds.length > 0 && selectableIds.every((id) => selectedIds.has(id))

  return (
    <div className="space-y-2 md:space-y-4 pt-2 md:pt-0">
      {/* 액션 바 */}
      <div className="flex items-center gap-2 flex-wrap">
        <Button
          type="button"
          variant={allSelected ? 'secondary' : 'outline'}
          onClick={handleSelectAll}
          disabled={!selectableIds.length}
        >
          {allSelected ? '선택 해제' : '전체 선택'}
        </Button>
        {onDelete && (
          <Button
            type="button"
            variant="destructive"
            onClick={handleBulkDelete}
            disabled={isDeleting || selectedIds.size === 0}
          >
            {isDeleting ? '삭제 중...' : `선택 삭제 (${selectedIds.size}개)`}
          </Button>
        )}
        {pagination && (
          <div className="flex items-center gap-2 ml-auto">
            <Select
              value={pagination.pageSize.toString()}
              onValueChange={(value) => {
                pagination.onPageSizeChange?.(parseInt(value, 10))
              }}
            >
              <SelectTrigger className="h-8 w-[70px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="12">12</SelectItem>
                <SelectItem value="24">24</SelectItem>
                <SelectItem value="48">48</SelectItem>
              </SelectContent>
            </Select>
            <span className="hidden md:inline text-sm text-muted-foreground">개</span>
          </div>
        )}
        {onUpload && (
          <Button type="button" onClick={onUpload}>
            <Upload className="h-4 w-4 mr-2" />
            업로드
          </Button>
        )}
      </div>

      {/* 메시지 */}
      {message && (
        <div
          className={cn(
            'p-3 rounded-md text-base',
            message.includes('실패')
              ? 'bg-destructive/10 text-destructive'
              : 'bg-primary/10 text-primary'
          )}
        >
          {message}
        </div>
      )}

      {/* 콘텐츠 목록 */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 md:gap-4">
        {contents.map((item) => (
          <ContentCard
            key={item.id}
            content={item}
            selected={selectedIds.has(item.id)}
            onToggle={toggleSelection}
            onRetry={onRetry ? handleRetry : undefined}
          />
        ))}
      </div>

      {/* 페이지네이션 */}
      {pagination && (
        <div className="flex items-center justify-center px-2 py-4">
          <div className="flex items-center gap-1 md:gap-2">
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => pagination.onPageChange(1)}
              disabled={pagination.currentPage === 1}
            >
              <ChevronsLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => pagination.onPageChange(pagination.currentPage - 1)}
              disabled={pagination.currentPage === 1}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>

            <div className="flex items-center justify-center min-w-[45px]">
              <span className="text-base font-medium">
                {pagination.currentPage} / {pagination.totalPages}
              </span>
            </div>

            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => pagination.onPageChange(pagination.currentPage + 1)}
              disabled={pagination.currentPage === pagination.totalPages}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8"
              onClick={() => pagination.onPageChange(pagination.totalPages)}
              disabled={pagination.currentPage === pagination.totalPages}
            >
              <ChevronsRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
