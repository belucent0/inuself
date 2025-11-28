'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { ContentSummary, ContentStatus, deleteContentsBulk } from '@/lib/api'

type Props = {
  contents: ContentSummary[]
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

const statusColors: Record<ContentStatus, string> = {
  QUEUED: '#666',
  PROCESSING: '#2196F3',
  SUMMARIZING: '#673AB7',
  COMPLETED: '#4CAF50',
  ASR_FAILED: '#F44336',
  SUMMARY_FAILED: '#E91E63',
  CANCELLED: '#FF9800',
}

export default function ContentList({ contents }: Props) {
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
      router.refresh()
      setTimeout(() => setMessage(''), 3000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '삭제 실패')
    } finally {
      setIsDeleting(false)
    }
  }

  if (!contents.length) {
    return <p>아직 처리된 콘텐츠가 없습니다. 파일을 업로드해 보세요.</p>
  }

  const allSelected =
    selectableIds.length > 0 && selectableIds.every((id) => selectedIds.has(id))

  return (
    <div>
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <button
          type="button"
          onClick={handleSelectAll}
          disabled={!selectableIds.length}
          style={{
            padding: '0.5rem 1rem',
            border: '1px solid #ccc',
            backgroundColor: allSelected ? '#e0e0e0' : '#fff',
            cursor: selectableIds.length ? 'pointer' : 'not-allowed',
          }}
        >
          {allSelected ? '선택 해제' : '전체 선택'}
        </button>
        <button
          type="button"
          onClick={handleBulkDelete}
          disabled={isDeleting || selectedIds.size === 0}
          style={{
            padding: '0.5rem 1rem',
            backgroundColor: '#F44336',
            color: '#fff',
            border: 'none',
            borderRadius: '4px',
            cursor: isDeleting || selectedIds.size === 0 ? 'not-allowed' : 'pointer',
            opacity: isDeleting || selectedIds.size === 0 ? 0.6 : 1,
          }}
        >
          {isDeleting ? '삭제 중...' : `선택 삭제 (${selectedIds.size}개)`}
        </button>
      </div>
      {message && (
        <p style={{ marginBottom: '1rem', color: message.includes('실패') ? '#F44336' : '#4CAF50' }}>
          {message}
        </p>
      )}
      <div className="list">
        {contents.map((item) => (
          <div
            key={item.id}
            className="card"
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: '0.5rem',
            }}
          >
            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.3rem',
                fontSize: '0.9rem',
              }}
            >
              <input
                type="checkbox"
                checked={selectedIds.has(item.id)}
                onChange={(event) => {
                  event.stopPropagation()
                  toggleSelection(item.id)
                }}
                onClick={(event) => event.stopPropagation()}
              />
              <span>선택</span>
            </label>
            <Link href={`/contents/${item.id}`} className="card-link">
              <h3>{item.filename}</h3>
              <p>
                <span
                  style={{
                    color: statusColors[item.status],
                    fontWeight: 'bold',
                    marginRight: '0.5rem',
                  }}
                >
                  [{statusLabels[item.status]}]
                </span>
                화자 수: {item.speakers.length || 0} · 재생 길이: {item.duration_seconds.toFixed(1)}초
              </p>
              <small>{new Date(item.created_at).toLocaleString()}</small>
            </Link>
          </div>
        ))}
      </div>
    </div>
  )
}

