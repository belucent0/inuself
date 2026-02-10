/**
 * ContentsPage - /contents 라우트
 */

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

  const handleRetry = async (id: string, type: 'download' | 'asr' | 'ocr' | 'summary') => {
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
    <div className="container mx-auto py-2 px-2 md:px-4">
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
