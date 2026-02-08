/**
 * ContentDetailPage - /contents/:id 라우트
 */

import { useParams } from 'react-router-dom'
import { ContentDetailLayout } from '@/features/content/components/ContentDetailLayout'
import { useContent } from '@/shared/hooks/useContents'
import { contentsApi } from '@/shared/services/endpoints/contents'

export function ContentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { content, isLoading, error, refetch } = useContent(id || '')

  const handleDelete = async () => {
    if (!id) return
    await contentsApi.deleteContents([id])
  }

  const handleRetry = async (
    type: 'asr' | 'ocr' | 'summary',
    options?: {
      minSpeakers?: number
      maxSpeakers?: number
      ocrMode?: string
      accuracyMode?: string
    }
  ) => {
    if (!id) return
    await contentsApi.retryProcessing(id, type, options)
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
      <div className="flex items-center justify-center h-full">
        <div className="text-center text-muted-foreground">
          콘텐츠를 찾을 수 없습니다.
        </div>
      </div>
    )
  }

  return (
    <ContentDetailLayout
      content={content}
      onDelete={handleDelete}
      onRetry={handleRetry}
      refetch={refetch}
    />
  )
}
