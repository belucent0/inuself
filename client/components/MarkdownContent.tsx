'use client'

import ReactMarkdown from 'react-markdown'
import { cn } from '@/lib/utils'

type MarkdownContentProps = {
  content: string
  className?: string
}

/**
 * 마크다운 콘텐츠를 렌더링하는 재사용 가능한 컴포넌트
 * 
 * @param content - 렌더링할 마크다운 텍스트
 * @param className - 추가 CSS 클래스 (기본적으로 markdown-content 클래스가 적용됨)
 */
export default function MarkdownContent({ content, className }: MarkdownContentProps) {
  if (!content || !content.trim()) {
    return null
  }

  return (
    <div className={cn("markdown-content", className)}>
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  )
}

