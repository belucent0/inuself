'use client'

import { useEffect, useState, useCallback } from 'react'
import { useSearchParams } from 'next/navigation'
import ContentList from '@/components/ContentList'
import DeleteQueuedButton from '@/components/DeleteQueuedButton'
import { listContents, ContentListResponse } from '@/lib/api'

export default function ContentsPage() {
  const [data, setData] = useState<ContentListResponse | null>(null)
  const [page, setPage] = useState(1)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const searchParams = useSearchParams()

  const pageSize = 10

  const fetchData = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const result = await listContents(page, pageSize)
      setData(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : '콘텐츠 목록을 불러오는데 실패했습니다.')
    } finally {
      setIsLoading(false)
    }
  }, [page, pageSize])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  // refresh 쿼리 파라미터가 있으면 자동 새로고침 (업로드 후 목록 페이지로 이동했을 때)
  useEffect(() => {
    const refresh = searchParams.get('refresh')
    if (refresh) {
      // refresh 파라미터가 있으면 데이터 새로고침
      fetchData()
    }
  }, [searchParams, fetchData])

  if (isLoading && !data) {
    return (
      <section>
        <h2>전사된 콘텐츠</h2>
        <p>로딩 중...</p>
      </section>
    )
  }

  if (error) {
    return (
      <section>
        <h2>전사된 콘텐츠</h2>
        <p style={{ color: '#F44336' }}>{error}</p>
      </section>
    )
  }

  if (!data) {
    return (
      <section>
        <h2>전사된 콘텐츠</h2>
        <p>데이터를 불러올 수 없습니다.</p>
      </section>
    )
  }

  const handleRefresh = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const result = await listContents(page, pageSize)
      setData(result)
    } catch (err) {
      setError(err instanceof Error ? err.message : '콘텐츠 목록을 불러오는데 실패했습니다.')
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <section>
      <h2>전사된 콘텐츠</h2>
      <DeleteQueuedButton />
      <ContentList 
        contents={data.items} 
        pagination={{
          currentPage: data.page,
          totalPages: data.total_pages,
          total: data.total,
          pageSize: data.page_size,
          onPageChange: setPage,
        }}
        onRefresh={handleRefresh}
      />
    </section>
  )
}


