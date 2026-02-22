/**
 * 스레드 목록 페이지용 훅 (TanStack Query 기반)
 */

import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { threadsApi } from '@/shared/services/endpoints/threads'
import type { Thread } from '@/shared/types'

const PAGE_SIZE = 20

export const threadListKeys = {
  all: ['thread-list'] as const,
  list: (page: number) => [...threadListKeys.all, page] as const,
}

interface UseThreadListResult {
  threads: Thread[]
  total: number
  page: number
  pageSize: number
  totalPages: number
  isLoading: boolean
  refetch: () => void
  setPage: (page: number) => void
}

export function useThreadList(): UseThreadListResult {
  const [page, setPage] = useState(1)
  const queryClient = useQueryClient()

  const { data, isLoading, refetch } = useQuery({
    queryKey: threadListKeys.list(page),
    queryFn: () =>
      threadsApi.getThreads({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
    staleTime: 30 * 1000,
    placeholderData: (previousData) => previousData,
  })

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return {
    threads: (data?.threads ?? []) as Thread[],
    total,
    page,
    pageSize: PAGE_SIZE,
    totalPages,
    isLoading,
    refetch: () => {
      queryClient.invalidateQueries({ queryKey: threadListKeys.all })
      refetch()
    },
    setPage,
  }
}
