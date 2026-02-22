/**
 * ThreadList - 스레드 목록 + 카드 렌더링 (날짜 그룹화)
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { History, Pencil, Trash2, ChevronLeft, ChevronRight, Check, X } from 'lucide-react'
import { formatDistanceToNow, format, isToday } from 'date-fns'
import { ko } from 'date-fns/locale'
import { Card } from '@/shared/components/ui/card'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Skeleton } from '@/shared/components/ui/skeleton'
import { Checkbox } from '@/shared/components/ui/checkbox'
import type { Thread } from '@/shared/types'

// ─── 날짜 그룹화 ────────────────────────────────────────────────────────────

function groupThreadsByDate(threads: Thread[]) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)
  const lastWeek = new Date(today)
  lastWeek.setDate(lastWeek.getDate() - 7)

  const groups: { label: string; threads: Thread[] }[] = [
    { label: '오늘', threads: [] },
    { label: '어제', threads: [] },
    { label: '이번 주', threads: [] },
    { label: '이전', threads: [] },
  ]

  threads.forEach((thread) => {
    const d = new Date(thread.updated_at * 1000)
    if (d >= today) {
      groups[0].threads.push(thread)
    } else if (d >= yesterday) {
      groups[1].threads.push(thread)
    } else if (d >= lastWeek) {
      groups[2].threads.push(thread)
    } else {
      groups[3].threads.push(thread)
    }
  })

  return groups.filter((g) => g.threads.length > 0)
}

// ─── 시간 포맷 ───────────────────────────────────────────────────────────────

function formatThreadTime(updatedAt: number): string {
  const date = new Date(updatedAt * 1000)
  if (isToday(date)) {
    return formatDistanceToNow(date, { addSuffix: true, locale: ko })
  }
  return format(date, 'yyyy-MM-dd')
}

// ─── ThreadCard ──────────────────────────────────────────────────────────────

interface ThreadCardProps {
  thread: Thread
  onDelete: (threadId: string) => void
  onRenameTitle: (threadId: string, newTitle: string) => void
  selected?: boolean
  selectionMode?: boolean
  onToggle?: (id: string) => void
}

function ThreadCard({ thread, onDelete, onRenameTitle, selected, selectionMode, onToggle }: ThreadCardProps) {
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [titleInput, setTitleInput] = useState(thread.title)

  const timeLabel = formatThreadTime(thread.updated_at)
  const messageCount = thread.message_count ?? 0

  const selectedContentIds =
    (thread.metadata?.source_options as { selected_content_ids?: string[] } | undefined)
      ?.selected_content_ids ?? []
  const docCount = selectedContentIds.length

  const handleCardClick = () => {
    if (selectionMode) {
      onToggle?.(thread.thread_id)
      return
    }
    if (!editing) navigate(`/chat/${thread.thread_id}`)
  }

  const handleSubmitRename = () => {
    if (titleInput.trim() && titleInput !== thread.title) {
      onRenameTitle(thread.thread_id, titleInput.trim())
    }
    setEditing(false)
  }

  const handleRename = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (editing) {
      handleSubmitRename()
    } else {
      setTitleInput(thread.title)
      setEditing(true)
    }
  }

  const handleCancelEdit = (e: React.MouseEvent) => {
    e.stopPropagation()
    setTitleInput(thread.title)
    setEditing(false)
  }

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation()
    onDelete(thread.thread_id)
  }

  return (
    <Card
      className={`px-5 py-4 cursor-pointer hover:shadow-md transition-all group ${selected ? 'bg-muted/30' : ''}`}
      onClick={handleCardClick}
    >
      {/* 1줄: 체크박스(선택 모드) + 제목 + 수정 버튼 */}
      <div className="flex items-center gap-2.5 min-w-0">
        {selectionMode && (
          <Checkbox
            checked={selected}
            onCheckedChange={() => onToggle?.(thread.thread_id)}
            onClick={(e) => e.stopPropagation()}
            className="shrink-0"
          />
        )}
        {editing ? (
          <div className="flex items-center gap-1.5 flex-1 min-w-0" onClick={(e) => e.stopPropagation()}>
            <Input
              autoFocus
              value={titleInput}
              onChange={(e) => setTitleInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleSubmitRename()
                } else if (e.key === 'Escape') {
                  setTitleInput(thread.title)
                  setEditing(false)
                }
              }}
              className="h-7 text-sm"
            />
            <Button size="icon" variant="ghost" className="h-7 w-7 shrink-0" onClick={handleRename}>
              <Check className="h-3.5 w-3.5" />
            </Button>
            <Button size="icon" variant="ghost" className="h-7 w-7 shrink-0" onClick={handleCancelEdit}>
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        ) : (
          <>
            <span className="text-lg font-medium truncate flex-1">{thread.title}</span>
            {!selectionMode && (
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                onClick={handleRename}
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
            )}
          </>
        )}
      </div>

      {/* 2줄: AI 응답 미리보기 */}
      {thread.first_message_preview && (
        <p className="text-base text-muted-foreground truncate mt-2">
          {thread.first_message_preview}
        </p>
      )}

      {/* 3줄: 메타 정보 + 삭제 버튼 */}
      <div className="flex items-center justify-between mt-2">
        <span className="text-sm text-muted-foreground">
          {timeLabel}
          {messageCount > 0 && ` · 메시지 ${messageCount}개`}
          {docCount > 0 && ` · 문서 ${docCount}개`}
        </span>
        {!selectionMode && (
          <Button
            size="icon"
            variant="ghost"
            className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity text-destructive hover:text-destructive shrink-0"
            onClick={handleDelete}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </Card>
  )
}

// ─── ThreadList ──────────────────────────────────────────────────────────────

interface PaginationInfo {
  currentPage: number
  totalPages: number
  onPageChange: (page: number) => void
}

interface ThreadListProps {
  threads: Thread[]
  pagination: PaginationInfo
  onDelete: (threadId: string) => void
  onRenameTitle: (threadId: string, newTitle: string) => void
  isLoading: boolean
  searchQuery: string
  selectedIds?: Set<string>
  selectionMode?: boolean
  onToggle?: (id: string) => void
}

export function ThreadList({
  threads,
  pagination,
  onDelete,
  onRenameTitle,
  isLoading,
  searchQuery,
  selectedIds,
  selectionMode,
  onToggle,
}: ThreadListProps) {
  const navigate = useNavigate()

  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full rounded-lg" />
        ))}
      </div>
    )
  }

  const filtered = threads.filter((t) =>
    t.title.toLowerCase().includes(searchQuery.toLowerCase())
  )

  if (filtered.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-center gap-3">
        <History className="h-12 w-12 text-muted-foreground/40" />
        <p className="text-muted-foreground">대화 기록이 없습니다</p>
        <Button variant="outline" onClick={() => navigate('/')}>
          새 대화 시작하기
        </Button>
      </div>
    )
  }

  const groups = groupThreadsByDate(filtered)

  return (
    <div className="space-y-8">
      {groups.map((group) => (
        <div key={group.label} className="space-y-2.5">
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider px-1">
            {group.label}
          </h3>
          {group.threads.map((thread) => (
            <ThreadCard
              key={thread.thread_id}
              thread={thread}
              onDelete={onDelete}
              onRenameTitle={onRenameTitle}
              selected={selectedIds?.has(thread.thread_id)}
              selectionMode={selectionMode}
              onToggle={onToggle}
            />
          ))}
        </div>
      ))}

      {/* 페이지네이션 */}
      {pagination.totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 pt-2">
          <Button
            variant="outline"
            disabled={pagination.currentPage <= 1}
            onClick={() => pagination.onPageChange(pagination.currentPage - 1)}
          >
            <ChevronLeft className="h-4 w-4" />
            이전
          </Button>
          <span className="text-sm text-muted-foreground">
            {pagination.currentPage} / {pagination.totalPages}
          </span>
          <Button
            variant="outline"
            disabled={pagination.currentPage >= pagination.totalPages}
            onClick={() => pagination.onPageChange(pagination.currentPage + 1)}
          >
            다음
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </div>
  )
}
