/**
 * 미디어 뷰어 분기 렌더러
 * content_type과 파일 확장자에 따라 적절한 뷰어를 선택
 */

import type { RefObject } from 'react'
import type { ContentDetail } from '../../types'
import { isImageFile, isPdfFile, isAudioFile, isVideoFile } from '../../types'
import { AudioVideoPlayer } from './AudioVideoPlayer'
import { PdfViewer } from './PdfViewer'
import { ImageViewer } from './ImageViewer'
import { FileText } from 'lucide-react'

interface MediaViewerProps {
  content: ContentDetail
  mediaRef: RefObject<HTMLMediaElement | null>
}

export function MediaViewer({ content, mediaRef }: MediaViewerProps) {
  const fileUrl = content.media_url || content.file_url

  if (!fileUrl) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
        <FileText className="h-8 w-8 mb-2 opacity-50" />
        <p className="text-xs">미리보기 불가</p>
      </div>
    )
  }

  if (content.content_type === 'AUDIO' || isAudioFile(content.filename) || isVideoFile(content.filename)) {
    return <AudioVideoPlayer src={fileUrl} filename={content.filename} mediaRef={mediaRef} />
  }

  if (isPdfFile(content.filename)) {
    return <PdfViewer src={fileUrl} />
  }

  if (content.content_type === 'PORTRAY' || isImageFile(content.filename)) {
    return <ImageViewer src={fileUrl} alt={content.title || content.filename} />
  }

  return (
    <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
      <FileText className="h-8 w-8 mb-2 opacity-50" />
      <p className="text-xs">미리보기 불가</p>
      <a
        href={fileUrl}
        download
        className="text-xs text-primary hover:underline mt-1"
      >
        다운로드
      </a>
    </div>
  )
}
