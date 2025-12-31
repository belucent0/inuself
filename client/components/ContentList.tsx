'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { useWebSocket } from '@/hooks/useWebSocket'
import { FileProgress, FileProgressEvent, FileStatus } from '@/types/file-progress'
import {
  ContentSummary,
  deleteContentsBulk,
  retryProcessing,
  getWebSocketBase,
} from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { ChevronsLeft, ChevronsRight, ChevronLeft, ChevronRight } from 'lucide-react'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { ContentItem } from './ContentItem'

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

export default function ContentList({ contents, pagination, onRefresh }: Props) {
  const router = useRouter()
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [isDeleting, setIsDeleting] = useState(false)
  const [message, setMessage] = useState<string>('')

  // 모든 파일의 진행 상태를 관리하는 Map
  const [progressMap, setProgressMap] = useState<Record<number, FileProgress>>({})

  // Global WebSocket 연결
  const wsBase = getWebSocketBase()
  // /ws/file-progress/global 엔드포인트 사용
  useWebSocket<FileProgressEvent>(`${wsBase}/file-progress/global`, {
    onMessage: (event) => {
      // file_progress 타입이고 file_id가 있는 경우 상태 업데이트
      if (event.type === 'file_progress' && event.file_id) {
        setProgressMap((prev) => ({
          ...prev,
          [event.file_id!]: {
            fileId: event.file_id!,
            status: (event.status as FileStatus) || 'processing',
            step: event.step || null,
            progress: event.progress || 0,
            message: event.message || '',
            lastUpdate: new Date(),
            isConnected: true,
          },
        }))
      }
    },
    reconnect: true,
    reconnectInterval: 3000,
  })

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
          <ContentItem
            key={item.id}
            item={item}
            selected={selectedIds.has(item.id)}
            onToggle={toggleSelection}
            onRetry={handleRetry}
            liveProgress={progressMap[item.id]}
          />
        ))}
      </div>

      {pagination && (
        <div className="relative flex items-center justify-between px-2 py-4">
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

          <div className="absolute left-1/2 transform -translate-x-1/2 flex items-center gap-1 md:gap-2">
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

            <div className="flex items-center justify-center min-w-[45px]">
              <span className="text-sm font-medium">
                {pagination.currentPage} / {pagination.totalPages}
              </span>
            </div>

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

        </div>
      )}
    </div>
  )
}
