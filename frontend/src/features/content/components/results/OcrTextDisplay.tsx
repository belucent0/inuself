/**
 * OCR/문서 텍스트 표시
 * - html_content → dangerouslySetInnerHTML
 * - ocr_text → pre 렌더링
 * - text_content → pre 렌더링
 */

import type { DocumentData } from '../../types'

interface OcrTextDisplayProps {
  document: DocumentData
}

export function OcrTextDisplay({ document }: OcrTextDisplayProps) {
  if (document.html_content) {
    return (
      <div className="space-y-3">
        <h4 className="text-sm font-medium">OCR 결과</h4>
        <div
          className="prose prose-sm dark:prose-invert max-w-none p-4 bg-muted/30 rounded-lg overflow-x-auto"
          dangerouslySetInnerHTML={{ __html: document.html_content }}
        />
      </div>
    )
  }

  const textContent = document.ocr_text || document.text_content
  if (!textContent) return null

  return (
    <div className="space-y-3">
      <h4 className="text-sm font-medium">
        {document.ocr_text ? 'OCR 결과' : '텍스트 내용'}
      </h4>
      <pre className="p-4 bg-muted/30 rounded-lg text-sm whitespace-pre-wrap overflow-x-auto max-h-96 overflow-y-auto">
        {textContent}
      </pre>
    </div>
  )
}
