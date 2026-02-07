/**
 * Contents 관련 훅
 */

import { useState, useEffect, useCallback } from 'react'
import { contentsApi, type ContentListResponse, type ContentListParams } from '@/shared/services/endpoints/contents'
import type { ContentSummary, ContentDetail } from '@/features/content/types'

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
  const [data, setData] = useState<ContentListResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [page, setPage] = useState(params?.page || 1)
  const [pageSize, setPageSize] = useState(params?.pageSize || 10)

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
