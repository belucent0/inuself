/**
 * 콘텐츠 검색 훅
 * SourceContextPopover에서 사용
 */

import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getContents } from '@/shared/services/endpoints/contents'
import type { ContentSummary } from '@/features/content/types'

export function useContentSearch(searchQuery: string) {
  const [debouncedQuery, setDebouncedQuery] = useState(searchQuery)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery), 300)
    return () => clearTimeout(timer)
  }, [searchQuery])

  const { data, isLoading } = useQuery({
    queryKey: ['contents', 'search', debouncedQuery],
    queryFn: () => getContents({ search: debouncedQuery || undefined, pageSize: 20 }),
    enabled: true,
    staleTime: 10_000,
  })

  const contents: ContentSummary[] = data?.contents ?? []

  return { contents, isLoading }
}
