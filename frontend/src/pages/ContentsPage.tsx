/**
 * ContentsPage - /contents 라우트
 */

import { Upload } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { ContentList } from '@/features/content'
import { useContents } from '@/shared/hooks/useContents'
import { contentsApi } from '@/shared/services/endpoints/contents'

export function ContentsPage() {
  const {
    contents,
    total,
    page,
    pageSize,
    totalPages,
    isLoading,
    refetch,
    setPage,
    setPageSize,
  } = useContents()

  const handleDelete = async (ids: string[]) => {
    await contentsApi.deleteContents(ids)
    refetch()
  }

  const handleRetry = async (id: string, type: 'asr' | 'ocr' | 'summary') => {
    await contentsApi.retryProcessing(id, type)
    refetch()
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  return (
    <div className="container mx-auto py-6 px-4">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">콘텐츠 관리</h1>
          <p className="text-muted-foreground">
            총 {total}개의 콘텐츠
          </p>
        </div>
        <Button>
          <Upload className="h-4 w-4 mr-2" />
          업로드
        </Button>
      </div>

      <ContentList
        contents={contents}
        pagination={{
          currentPage: page,
          totalPages,
          total,
          pageSize,
          onPageChange: setPage,
          onPageSizeChange: setPageSize,
        }}
        onDelete={handleDelete}
        onRetry={handleRetry}
      />
    </div>
  )
}
