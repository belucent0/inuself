/**
 * Contents 관련 훅 (TanStack Query 기반)
 */

import { useCallback, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { contentsApi, type ContentListResponse, type ContentListParams } from '@/shared/services/endpoints/contents'
import type { ContentSummary, ContentDetail, ContentStatus } from '@/features/content/types'
import type { FileProgressEvent } from '@/features/upload/types'
import { useFileProgressSSE } from './useFileProgressSSE'

/** 업로드 완료 후 목록 갱신을 트리거하는 이벤트 */
export const CONTENTS_REFRESH_EVENT = 'contents:refresh'
export function dispatchContentsRefresh() {
  window.dispatchEvent(new Event(CONTENTS_REFRESH_EVENT))
}

/**
 * 상태 전환 감지 시 콘텐츠 상세 정보를 갱신할지 판단하는 함수
 */
const isSignificantStatusChange = (oldStatus: ContentStatus, newStatus: ContentStatus): boolean => {
  const startingStates = ['QUEUED', 'PULLING']
  const completingStates = ['COMPLETED', 'FAILED', 'DOWNLOAD_FAILED']

  const isStart = startingStates.includes(newStatus)
  const isEnd = completingStates.includes(newStatus)

  return oldStatus !== newStatus && (isStart || isEnd)
}

// Query Keys
export const contentKeys = {
  all: ['contents'] as const,
  lists: () => [...contentKeys.all, 'list'] as const,
  list: (params: { page: number; pageSize: number }) => [...contentKeys.lists(), params] as const,
  details: () => [...contentKeys.all, 'detail'] as const,
  detail: (id: string) => [...contentKeys.details(), id] as const,
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
  const queryClient = useQueryClient()
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

  // TanStack Query로 데이터 페칭
  const {
    data,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: contentKeys.list({ page, pageSize }),
    queryFn: () => contentsApi.getContents({ ...params, page, pageSize }),
    staleTime: 30 * 1000, // 30초
    placeholderData: (previousData) => previousData, // 페이지 전환 시 이전 데이터 유지
  })

  // 커스텀 이벤트로 즉시 갱신
  useEffect(() => {
    const handler = () => {
      queryClient.invalidateQueries({ queryKey: contentKeys.lists() })
    }
    window.addEventListener(CONTENTS_REFRESH_EVENT, handler)
    return () => window.removeEventListener(CONTENTS_REFRESH_EVENT, handler)
  }, [queryClient])

  /**
   * SSE 이벤트 핸들러: 개별 콘텐츠 상태 업데이트
   */
  const handleFileProgress = useCallback((event: FileProgressEvent) => {
    // 캐시된 데이터 직접 업데이트
    queryClient.setQueryData<ContentListResponse>(
      contentKeys.list({ page, pageSize }),
      (prev) => {
        if (!prev) return prev

        const updatedContents = prev.contents.map((item) => {
          if (item.id !== event.file_id && item.id !== event.content_id) {
            return item
          }

          const oldStatus = item.status
          const newStatus = (event.status ? event.status.toUpperCase() : event.status) as ContentStatus

          // 상태 변경 감지 → 개별 콘텐츠 상세 조회
          if (isSignificantStatusChange(oldStatus, newStatus)) {
            if (updateTaskRef.current) {
              clearTimeout(updateTaskRef.current)
            }
            updateTaskRef.current = setTimeout(async () => {
              try {
                const updated = await contentsApi.getContent(item.id)
                // 상세 캐시 업데이트
                queryClient.setQueryData(contentKeys.detail(item.id), updated)
                // 목록 캐시도 업데이트
                queryClient.setQueryData<ContentListResponse>(
                  contentKeys.list({ page, pageSize }),
                  (current) => {
                    if (!current) return current
                    return {
                      ...current,
                      contents: current.contents.map((c) =>
                        c.id === item.id ? (updated as ContentSummary) : c
                      ),
                    }
                  }
                )
              } catch (err) {
                console.error(`Failed to update content ${item.id}:`, err)
              }
            }, 500)
          }

          // 즉시 상태 + progress 업데이트
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
      }
    )
  }, [queryClient, page, pageSize])

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
    error: error as Error | null,
    refetch,
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
  const queryClient = useQueryClient()
  const { addListener, removeListener } = useFileProgressSSE()

  const {
    data: content,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: contentKeys.detail(id),
    queryFn: () => contentsApi.getContent(id),
    enabled: !!id,
    staleTime: 30 * 1000, // 30초
  })

  // SSE로 해당 콘텐츠의 상태 변경 감지
  useEffect(() => {
    if (!id) return

    const handleProgress = (event: FileProgressEvent) => {
      if (event.file_id === id) {
        // 상태 변경 시 캐시 무효화하여 refetch 트리거
        queryClient.invalidateQueries({ queryKey: contentKeys.detail(id) })
      }
    }

    addListener(handleProgress)
    return () => removeListener(handleProgress)
  }, [id, queryClient, addListener, removeListener])

  return {
    content: content || null,
    isLoading,
    error: error as Error | null,
    refetch,
  }
}
