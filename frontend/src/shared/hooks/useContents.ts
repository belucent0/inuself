/**
 * Contents 관련 훅
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { contentsApi, type ContentListResponse, type ContentListParams } from '@/shared/services/endpoints/contents'
import type { ContentSummary, ContentDetail, ContentStatus } from '@/features/content/types'

/** 업로드 완료 후 목록 갱신을 트리거하는 이벤트 */
export const CONTENTS_REFRESH_EVENT = 'contents:refresh'
export function dispatchContentsRefresh() {
  window.dispatchEvent(new Event(CONTENTS_REFRESH_EVENT))
}

const PROCESSING_STATUSES: ContentStatus[] = [
  'QUEUED', 'PULLING', 'PROCESSING', 'OCR_PROCESSING', 'SUMMARY_QUEUED', 'SUMMARIZING',
]
const POLL_INTERVAL = 5000

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
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

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
      setData(result)
    } catch (err) {
      setError(err as Error)
    } finally {
      setIsLoading(false)
    }
  }, [params, page, pageSize])

  useEffect(() => {
    fetchContents()
  }, [fetchContents])

  // 커스텀 이벤트로 즉시 갱신
  useEffect(() => {
    const handler = () => fetchContents()
    window.addEventListener(CONTENTS_REFRESH_EVENT, handler)
    return () => window.removeEventListener(CONTENTS_REFRESH_EVENT, handler)
  }, [fetchContents])

  // 처리 중 콘텐츠가 있으면 자동 폴링
  const hasProcessing = data?.contents?.some((c) => PROCESSING_STATUSES.includes(c.status)) ?? false

  useEffect(() => {
    if (hasProcessing) {
      pollRef.current = setInterval(() => fetchContents(), POLL_INTERVAL)
    } else if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [hasProcessing, fetchContents])

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
