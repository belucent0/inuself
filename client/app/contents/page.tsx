'use client'

import { useEffect, useState } from 'react'
import ContentList from '@/components/ContentList'
import DeleteQueuedButton from '@/components/DeleteQueuedButton'
import { listContents, ContentListResponse } from '@/lib/api'

export default function ContentsPage() {
  const [data, setData] = useState<ContentListResponse | null>(null)
  const [page, setPage] = useState(1)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const pageSize = 20

  useEffect(() => {
    const fetchData = async () => {
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
    fetchData()
  }, [page])

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


