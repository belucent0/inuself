/**
 * ContentDetailPage - /contents/:id 라우트
 */

import { useParams } from 'react-router-dom'
import { ContentDetailView } from '@/features/content'
import { useContent } from '@/shared/hooks/useContents'
import { contentsApi } from '@/shared/services/endpoints/contents'

export function ContentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { content, isLoading, error, refetch } = useContent(id || '')

  const handleDelete = async () => {
    if (!id) return
    await contentsApi.deleteContents([id])
  }

  const handleRetry = async (type: 'asr' | 'ocr' | 'summary') => {
    if (!id) return
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

  if (error || !content) {
    return (
      <div className="container mx-auto py-6 px-4">
        <div className="text-center text-muted-foreground">
          콘텐츠를 찾을 수 없습니다.
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto py-6 px-4">
      <ContentDetailView
        content={content}
        onDelete={handleDelete}
        onRetry={handleRetry}
      />
    </div>
  )
}
