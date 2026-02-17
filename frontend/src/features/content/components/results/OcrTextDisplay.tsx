/**
 * OCR/문서 텍스트 표시
 * - HTML 콘텐츠 감지 → dangerouslySetInnerHTML (markdown-content 스타일)
 * - 일반 텍스트 → pre 렌더링
 * - TranscriptionSegments와 동일한 flex 레이아웃으로 전체 공간 활용
 */

import type { DocumentData } from '../../types'
import { Badge } from '@/shared/components/ui/badge'
import { FileText } from 'lucide-react'

interface OcrTextDisplayProps {
  document: DocumentData
}

const HTML_TAG_PATTERN = /<(?:html|head|body|div|p|h[1-6]|span|table|ul|ol|li|br|img)\b[^>]*>/i

function isHtmlContent(text: string): boolean {
  return HTML_TAG_PATTERN.test(text)
}

export function OcrTextDisplay({ document }: OcrTextDisplayProps) {
  const htmlContent = document.html_content
  const textContent = document.ocr_text || document.text_content
  const content = htmlContent || textContent
  if (!content) return null

  const shouldRenderAsHtml = htmlContent || (textContent && isHtmlContent(textContent))
  const label = document.ocr_text || document.html_content ? 'OCR 결과' : '텍스트 내용'

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* 헤더: 고정 */}
      <div className="shrink-0 flex items-center px-4 py-2 border-b">
        <Badge variant="default" className="gap-1 text-xs font-medium">
          <FileText className="h-4 w-4" />
          {label}
        </Badge>
      </div>

      {/* 콘텐츠: 스크롤 */}
      <div className="flex-1 overflow-y-auto p-4">
        {shouldRenderAsHtml ? (
          <div
            className="markdown-content max-w-none"
            dangerouslySetInnerHTML={{ __html: content }}
          />
        ) : (
          <pre className="text-sm whitespace-pre-wrap leading-relaxed">
            {content}
          </pre>
        )}
      </div>
    </div>
  )
}
