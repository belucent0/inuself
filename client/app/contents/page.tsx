'use client'

import { useEffect, useState, useCallback } from 'react'
import { useLocalStorage } from '@/hooks/use-local-storage'
import { useSearchParams } from 'next/navigation'
import ContentList from '@/components/ContentList'
import { listContents, ContentListResponse } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import PageHeader from '@/components/PageHeader'

export default function ContentsPage() {
  const [data, setData] = useState<ContentListResponse | null>(null)
  const [page, setPage] = useState(1)
  // 모바일에서는 5, 데스크톱에서는 10을 기본값으로 설정
  // 모바일에서는 5, 데스크톱에서는 10을 기본값으로 설정 (localStorage에 저장된 값이 있으면 사용)
  const [pageSize, setPageSize] = useLocalStorage<number>('content_list_page_size',
    typeof window !== 'undefined' && window.innerWidth < 768 ? 5 : 10
  )
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const searchParams = useSearchParams()



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

  const breadcrumbItems = [
    { label: '홈', href: '/' },
    { label: '콘텐츠' },
  ]

  if (isLoading && !data) {
    return (
      <div>
        <PageHeader items={breadcrumbItems} />
        <Card>
          <CardContent className="pt-6">
            <p className="text-muted-foreground">로딩 중...</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error) {
    return (
      <div>
        <PageHeader items={breadcrumbItems} />
        <Card>
          <CardContent className="pt-6">
            <p className="text-destructive">{error}</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!data) {
    return (
      <div>
        <PageHeader items={breadcrumbItems} />
        <Card>
          <CardContent className="pt-6">
            <p className="text-muted-foreground">데이터를 불러올 수 없습니다.</p>
          </CardContent>
        </Card>
      </div>
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
    <div>
      <PageHeader items={breadcrumbItems} />
      <ContentList
        contents={data.items}
        pagination={{
          currentPage: data.page,
          totalPages: data.total_pages,
          total: data.total,
          pageSize: data.page_size,
          onPageChange: setPage,
          onPageSizeChange: (newPageSize) => {
            setPageSize(newPageSize)
            setPage(1) // 페이지 크기 변경 시 첫 페이지로 이동
          },
        }}
        onRefresh={handleRefresh}
      />
    </div>
  )
}
