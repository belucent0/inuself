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
import { OcrRetryModal, OcrMode, AccuracyMode } from '@/components/OcrRetryModal'
import { AsrRetryModal, AsrRetryOptions } from '@/components/AsrRetryModal'

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
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [isDeleting, setIsDeleting] = useState(false)
  const [message, setMessage] = useState<string>('')

  // 재시도 모달 상태
  const [showOcrRetryModal, setShowOcrRetryModal] = useState(false)
  const [showAsrRetryModal, setShowAsrRetryModal] = useState(false)
  const [retryTargetContent, setRetryTargetContent] = useState<ContentSummary | null>(null)
  const [isRetrying, setIsRetrying] = useState(false)

  // 모든 파일의 진행 상태를 관리하는 Map
  const [progressMap, setProgressMap] = useState<Record<string, FileProgress>>({})

  // 콘텐츠 목록을 내부 상태로 관리 (소켓 이벤트로 실시간 업데이트)
  const [localContents, setLocalContents] = useState<ContentSummary[]>(contents)

  // contents prop이 변경되면 localContents 업데이트
  useEffect(() => {
    setLocalContents(contents)
  }, [contents])

  // Global WebSocket 연결
  const wsBase = getWebSocketBase()
  // /ws/file-progress/global 엔드포인트 사용
  useWebSocket<FileProgressEvent>(`${wsBase}/file-progress/global`, {
    onMessage: (event) => {
      // content_created 타입인 경우 목록 새로고침
      if (event.type === 'content_created') {
        console.log('[WebSocket] Content created event received:', event)
        // 목록 새로고침
        if (onRefresh) {
          onRefresh()
        }
        return
      }
      
      // file_progress 타입이고 file_id가 있는 경우 상태 업데이트
      if (event.type === 'file_progress' && event.file_id) {
        const fileId = event.file_id
        
        // progressMap 업데이트
        setProgressMap((prev) => ({
          ...prev,
          [fileId]: {
            fileId: fileId,
            status: (event.status as FileStatus) || 'processing',
            step: event.step || null,
            progress: event.progress || 0,
            message: event.message || '',
            lastUpdate: new Date(),
            isConnected: true,
          },
        }))

        // metadata가 있으면 콘텐츠 목록 업데이트
        if (event.metadata) {
          setLocalContents((prev) =>
            prev.map((item) => {
              if (item.id !== fileId) {
                return item
              }

              // 업데이트할 필드들
              const updates: Partial<ContentSummary> = {}

              // 제목 업데이트
              if (event.metadata?.title !== undefined) {
                updates.title = event.metadata.title
              }

              // duration_seconds 업데이트
              if (event.metadata?.duration_seconds !== undefined) {
                updates.duration_seconds = event.metadata.duration_seconds
                // transcription 객체도 업데이트
                if (item.transcription) {
                  updates.transcription = {
                    ...item.transcription,
                    duration_seconds: event.metadata.duration_seconds,
                  }
                }
              }

              // speakers 업데이트
              if (event.metadata?.speakers !== undefined) {
                updates.speakers = event.metadata.speakers
                // transcription 객체도 업데이트
                if (item.transcription) {
                  updates.transcription = {
                    ...item.transcription,
                    speakers: event.metadata.speakers,
                  }
                }
              }

              // page_count 업데이트 (document 객체 내부)
              if (event.metadata?.page_count !== undefined && item.document) {
                updates.document = {
                  ...item.document,
                  page_count: event.metadata.page_count,
                }
              }

              return {
                ...item,
                ...updates,
              }
            })
          )
        }
      }
    },
    reconnect: true,
    reconnectInterval: 3000,
  })

  const selectableIds = useMemo(() => localContents.map((content) => content.id), [localContents])

  useEffect(() => {
    setSelectedIds((prev) => {
      if (!prev.size) {
        return prev
      }
      const next = new Set<string>()
      selectableIds.forEach((id) => {
        if (prev.has(id)) {
          next.add(id)
        }
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

  const handleRetry = (contentId: string, type: 'asr' | 'ocr' | 'summary', event: React.MouseEvent) => {
    event.stopPropagation()

    // 재시도 대상 콘텐츠 찾기
    const targetContent = localContents.find((c) => c.id === contentId)
    if (!targetContent) return

    if (type === 'ocr') {
      setRetryTargetContent(targetContent)
      setShowOcrRetryModal(true)
      return
    }

    if (type === 'asr') {
      setRetryTargetContent(targetContent)
      setShowAsrRetryModal(true)
      return
    }

    // LLM 요약만 confirm으로 처리
    if (!confirm('LLM 요약을 다시 시도하시겠습니까?')) {
      return
    }

    handleSummaryRetry(contentId)
  }

  // LLM 요약 재처리
  const handleSummaryRetry = async (contentId: string) => {
    try {
      const result = await retryProcessing(contentId, 'summary')
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

  // OCR 재처리 확인 핸들러
  const handleOcrRetryConfirm = async (ocrMode: OcrMode, accuracyMode: AccuracyMode) => {
    if (!retryTargetContent) return

    setIsRetrying(true)
    try {
      const result = await retryProcessing(retryTargetContent.id, 'ocr', undefined, undefined, ocrMode, accuracyMode)
      setMessage(result.message)
      if (onRefresh) {
        onRefresh()
      } else {
        router.refresh()
      }
      setTimeout(() => setMessage(''), 3000)
      setShowOcrRetryModal(false)
      setRetryTargetContent(null)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '재처리 실패')
    } finally {
      setIsRetrying(false)
    }
  }

  // ASR 재처리 확인 핸들러
  const handleAsrRetryConfirm = async (options: AsrRetryOptions) => {
    if (!retryTargetContent) return

    setIsRetrying(true)
    try {
      const result = await retryProcessing(
        retryTargetContent.id,
        'asr',
        options.minSpeakers,
        options.maxSpeakers,
        undefined,
        undefined,
        options.accuracyMode
      )
      setMessage(result.message)
      if (onRefresh) {
        onRefresh()
      } else {
        router.refresh()
      }
      setTimeout(() => setMessage(''), 3000)
      setShowAsrRetryModal(false)
      setRetryTargetContent(null)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '재처리 실패')
    } finally {
      setIsRetrying(false)
    }
  }

  if (!localContents.length) {
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
        {localContents.map((item) => (
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
              className="h-8 w-8 hover:bg-background hover:text-foreground md:hover:bg-accent md:hover:text-accent-foreground active:bg-accent active:text-accent-foreground"
              onClick={(e) => {
                e.currentTarget.blur()
                pagination.onPageChange(1)
              }}
              disabled={pagination.currentPage === 1}
              title="Go to first page"
            >
              <ChevronsLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8 hover:bg-background hover:text-foreground md:hover:bg-accent md:hover:text-accent-foreground active:bg-accent active:text-accent-foreground"
              onClick={(e) => {
                e.currentTarget.blur()
                pagination.onPageChange(pagination.currentPage - 1)
              }}
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
              className="h-8 w-8 hover:bg-background hover:text-foreground md:hover:bg-accent md:hover:text-accent-foreground active:bg-accent active:text-accent-foreground"
              onClick={(e) => {
                e.currentTarget.blur()
                pagination.onPageChange(pagination.currentPage + 1)
              }}
              disabled={pagination.currentPage === pagination.totalPages}
              title="Go to next page"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8 hover:bg-background hover:text-foreground md:hover:bg-accent md:hover:text-accent-foreground active:bg-accent active:text-accent-foreground"
              onClick={(e) => {
                e.currentTarget.blur()
                pagination.onPageChange(pagination.totalPages)
              }}
              disabled={pagination.currentPage === pagination.totalPages}
              title="Go to last page"
            >
              <ChevronsRight className="h-4 w-4" />
            </Button>
          </div>

        </div>
      )}

      {/* OCR 재처리 모달 */}
      {retryTargetContent && (
        <OcrRetryModal
          open={showOcrRetryModal}
          onOpenChange={(open) => {
            setShowOcrRetryModal(open)
            if (!open) setRetryTargetContent(null)
          }}
          filename={retryTargetContent.filename}
          onConfirm={handleOcrRetryConfirm}
          isLoading={isRetrying}
        />
      )}

      {/* ASR 재처리 모달 */}
      <AsrRetryModal
        open={showAsrRetryModal}
        onOpenChange={(open) => {
          setShowAsrRetryModal(open)
          if (!open) setRetryTargetContent(null)
        }}
        onConfirm={handleAsrRetryConfirm}
        isLoading={isRetrying}
      />
    </div>
  )
}
