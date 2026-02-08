/**
 * Contents 관련 훅
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { contentsApi, type ContentListResponse, type ContentListParams } from '@/shared/services/endpoints/contents'
import type { ContentSummary, ContentDetail, ContentStatus } from '@/features/content/types'
import type { FileProgressEvent } from '@/features/upload/types'
import { useFileProgressSSE } from './useFileProgressSSE'

/** API 재조회 시 클라이언트 측 ephemeral 필드(progress, updated_at)를 보존 */
function mergeEphemeralState(
  fresh: ContentListResponse,
  prev: ContentListResponse | null,
): ContentListResponse {
  if (!prev) return fresh
  const prevMap = new Map(prev.contents.map((c: ContentSummary) => [c.id, c]))
  return {
    ...fresh,
    contents: fresh.contents.map((item) => {
      const existing = prevMap.get(item.id)
      if (!existing) return item
      // 같은 상태이면 SSE로 설정된 progress/updated_at 보존
      if (existing.status === item.status && existing.progress != null) {
        return { ...item, progress: existing.progress, updated_at: existing.updated_at || item.updated_at }
      }
      return item
    }),
  }
}

/** 업로드 완료 후 목록 갱신을 트리거하는 이벤트 */
export const CONTENTS_REFRESH_EVENT = 'contents:refresh'
export function dispatchContentsRefresh() {
  window.dispatchEvent(new Event(CONTENTS_REFRESH_EVENT))
}

/**
 * 상태 전환 감지 시 콘텐츠 상세 정보를 갱신할지 판단하는 함수
 *
 * 다음과 같은 상태 전환은 메타데이터 변경을 포함할 수 있으므로
 * 개별 콘텐츠를 다시 조회합니다.
 */
const isSignificantStatusChange = (oldStatus: ContentStatus, newStatus: ContentStatus): boolean => {
  // 처리 시작 또는 완료 단계의 전환은 항상 갱신
  const startingStates = ['QUEUED', 'PULLING']
  const completingStates = ['COMPLETED', 'FAILED', 'DOWNLOAD_FAILED']

  const isStart = startingStates.includes(newStatus)
  const isEnd = completingStates.includes(newStatus)

  return oldStatus !== newStatus && (isStart || isEnd)
}

interface UseContentsResult {
  contents: ContentSummary[]
  total: number
  page: number
  pageSize: number
  totalPages: number
  isLoading: boolean
  error: Error | null
  refetch: () => void
  setPage: (page: number) => void
  setPageSize: (pageSize: number) => void
}

export function useContents(params?: ContentListParams): UseContentsResult {
  const [searchParams, setSearchParams] = useSearchParams()
  const [data, setData] = useState<ContentListResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const { addListener, removeListener } = useFileProgressSSE()
  const updateTaskRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const page = Number(searchParams.get('page')) || params?.page || 1
  const pageSize = Number(searchParams.get('pageSize')) || params?.pageSize || 12

  const setPage = (p: number) => {
    setSearchParams((prev) => {
      prev.set('page', String(p))
      return prev
    })
  }

  const setPageSize = (size: number) => {
    setSearchParams((prev) => {
      prev.set('pageSize', String(size))
      prev.set('page', '1')
      return prev
    })
  }

  const fetchContents = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const result = await contentsApi.getContents({ ...params, page, pageSize })
      setData((prev) => mergeEphemeralState(result, prev))
    } catch (err) {
      setError(err as Error)
    } finally {
      setIsLoading(false)
    }
  }, [params, page, pageSize])

  // 초기 로드 및 페이지/페이지사이즈 변경 시에만 호출
  useEffect(() => {
    fetchContents()
  }, [page, pageSize])

  // 커스텀 이벤트로 즉시 갱신
  useEffect(() => {
    const handler = () => {
      setIsLoading(true)
      setError(null)
      contentsApi.getContents({ ...params, page, pageSize }).then((result) => {
        setData((prev) => mergeEphemeralState(result, prev))
      }).catch((err) => {
        setError(err as Error)
      }).finally(() => {
        setIsLoading(false)
      })
    }
    window.addEventListener(CONTENTS_REFRESH_EVENT, handler)
    return () => window.removeEventListener(CONTENTS_REFRESH_EVENT, handler)
  }, [params, page, pageSize])

  /**
   * SSE 이벤트 핸들러: 개별 콘텐츠 상태 업데이트
   */
  const handleFileProgress = useCallback((event: FileProgressEvent) => {
    setData((prev) => {
      if (!prev) return prev

      const updatedContents = prev.contents.map((item) => {
        if (item.id !== event.file_id && item.id !== event.content_id) {
          return item
        }

        const oldStatus = item.status
        const newStatus = (event.status ? event.status.toUpperCase() : event.status) as ContentStatus

        // 상태 변경 감지
        if (isSignificantStatusChange(oldStatus, newStatus)) {
          // 상태 전환 감지 → 개별 콘텐츠 상세 조회 (약간의 지연 후)
          if (updateTaskRef.current) {
            clearTimeout(updateTaskRef.current)
          }
          updateTaskRef.current = setTimeout(async () => {
            try {
              const updated = await contentsApi.getContent(item.id)
              // 목록의 해당 항목 업데이트
              setData((current) => {
                if (!current) return current
                return {
                  ...current,
                  contents: current.contents.map((c) =>
                    c.id === item.id ? (updated as ContentSummary) : c
                  ),
                }
              })
            } catch (err) {
              console.error(`Failed to update content ${item.id}:`, err)
            }
          }, 500)
        }

        // 즉시 상태 + progress 업데이트 (같은 상태 내에서는 단조증가)
        const newProgress = (newStatus === oldStatus)
          ? Math.max(event.progress ?? 0, item.progress ?? 0)
          : event.progress

        return {
          ...item,
          status: newStatus,
          progress: newProgress,
          ...(oldStatus !== newStatus ? { updated_at: new Date().toISOString() } : {}),
        }
      })

      return { ...prev, contents: updatedContents }
    })
  }, [])

  /**
   * SSE 리스너 등록/제거
   */
  useEffect(() => {
    addListener(handleFileProgress)
    return () => {
      removeListener(handleFileProgress)
      if (updateTaskRef.current) {
        clearTimeout(updateTaskRef.current)
      }
    }
  }, [addListener, removeListener, handleFileProgress])

  return {
    contents: data?.contents || [],
    total: data?.total || 0,
    page: data?.page || page,
    pageSize: data?.page_size || pageSize,
    totalPages: data?.total_pages || 0,
    isLoading,
    error,
    refetch: fetchContents,
    setPage,
    setPageSize,
  }
}

interface UseContentResult {
  content: ContentDetail | null
  isLoading: boolean
  error: Error | null
  refetch: () => void
}

export function useContent(id: string): UseContentResult {
  const [content, setContent] = useState<ContentDetail | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  const fetchContent = useCallback(async () => {
    if (!id) return
    setIsLoading(true)
    setError(null)
    try {
      const result = await contentsApi.getContent(id)
      setContent(result)
    } catch (err) {
      setError(err as Error)
    } finally {
      setIsLoading(false)
    }
  }, [id])

  useEffect(() => {
    fetchContent()
  }, [fetchContent])

  return {
    content,
    isLoading,
    error,
    refetch: fetchContent,
  }
}
