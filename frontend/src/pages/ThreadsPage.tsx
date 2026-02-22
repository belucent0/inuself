/**
 * ThreadsPage - /threads 라우트
 * 퍼플렉시티 스타일 대화 기록 전용 페이지
 */

import { useState, useMemo, useCallback } from 'react'
import { toast } from 'sonner'
import { Input } from '@/shared/components/ui/input'
import { Button } from '@/shared/components/ui/button'
import { DeleteConfirmDialog } from '@/shared/components/DeleteConfirmDialog'
import { ThreadList } from '@/features/thread/components/ThreadList'
import { useThreadList } from '@/shared/hooks/useThreadList'
import { threadsApi } from '@/shared/services/endpoints/threads'

export function ThreadsPage() {
  const [searchQuery, setSearchQuery] = useState('')
  const { threads, page, totalPages, isLoading, refetch, setPage } = useThreadList()

  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [selectionMode, setSelectionMode] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [optimisticallyHidden, setOptimisticallyHidden] = useState<Set<string>>(new Set())

  const visibleThreads = useMemo(
    () => threads.filter((t) => !optimisticallyHidden.has(t.thread_id)),
    [threads, optimisticallyHidden]
  )

  const allThreadIds = useMemo(() => visibleThreads.map((t) => t.thread_id), [visibleThreads])
  const allSelected = allThreadIds.length > 0 && allThreadIds.every((id) => selectedIds.has(id))

  const handleToggle = (id: string) => {
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

  const handleSelectionModeToggle = () => {
    setSelectionMode((prev) => {
      if (prev) setSelectedIds(new Set())
      return !prev
    })
  }

  const handleSelectAll = () => {
    setSelectedIds(() => {
      if (allSelected) return new Set()
      return new Set(allThreadIds)
    })
  }

  const handleBulkDeleteConfirm = useCallback(async () => {
    const idsToDelete = Array.from(selectedIds)
    setDeleteDialogOpen(false)
    setOptimisticallyHidden(new Set(idsToDelete))
    setSelectedIds(new Set())
    setSelectionMode(false)

    try {
      await toast.promise(threadsApi.bulkDeleteThreads(idsToDelete), {
        loading: `${idsToDelete.length}개 대화 삭제 중...`,
        success: `${idsToDelete.length}개 대화가 삭제되었습니다.`,
        error: '삭제에 실패했습니다.',
      })
      refetch()
    } catch {
      // toast.promise가 에러를 표시하므로 롤백만 수행
    } finally {
      setOptimisticallyHidden(new Set())
    }
  }, [selectedIds, refetch])

  const handleDelete = async (threadId: string) => {
    if (!confirm('이 대화를 삭제하시겠습니까?')) return
    await threadsApi.deleteThread(threadId)
    refetch()
  }

  const handleRenameTitle = async (threadId: string, newTitle: string) => {
    await threadsApi.updateThreadTitle(threadId, newTitle)
    refetch()
  }

  return (
    <div className="container mx-auto -mt-4 md:-mt-8 pt-4 pb-4 px-4 max-w-5xl">
      {/* 검색 */}
      <div className="mb-4">
        <Input
          placeholder="대화 검색..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full"
        />
      </div>

      {/* 액션 바 */}
      <div className="flex items-center gap-2 flex-nowrap mb-4">
        <Button
          type="button"
          size="sm"
          variant={selectionMode ? 'secondary' : 'outline'}
          onClick={handleSelectionModeToggle}
          className="h-8 px-2.5 text-xs"
        >
          {selectionMode ? '취소' : '선택'}
        </Button>
        {selectionMode && (
          <Button
            type="button"
            size="sm"
            variant={allSelected ? 'secondary' : 'outline'}
            onClick={handleSelectAll}
            disabled={!allThreadIds.length}
            className="h-8 px-2.5 text-xs"
          >
            전체
          </Button>
        )}
        {selectionMode && (
          <Button
            type="button"
            size="sm"
            variant="destructive"
            onClick={() => setDeleteDialogOpen(true)}
            disabled={selectedIds.size === 0}
            className="h-8 px-2.5 text-xs"
          >
            삭제({selectedIds.size}개)
          </Button>
        )}
      </div>

      {/* 목록 */}
      <ThreadList
        threads={visibleThreads}
        pagination={{ currentPage: page, totalPages, onPageChange: setPage }}
        onDelete={handleDelete}
        onRenameTitle={handleRenameTitle}
        isLoading={isLoading}
        searchQuery={searchQuery}
        selectedIds={selectedIds}
        selectionMode={selectionMode}
        onToggle={handleToggle}
      />

      {/* 벌크 삭제 확인 다이얼로그 */}
      <DeleteConfirmDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        onConfirm={handleBulkDeleteConfirm}
        title="대화 삭제"
        description={`선택한 ${selectedIds.size}개의 대화를 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`}
      />
    </div>
  )
}
