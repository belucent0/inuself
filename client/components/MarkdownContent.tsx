'use client'

import ReactMarkdown from 'react-markdown'
import * as React from 'react'
import { cn } from '@/lib/utils'

interface SearchSource {
  position: number
  title: string
  url: string
  snippet: string
  engine: string
}

type MarkdownContentProps = {
  content: string
  className?: string
  sources?: SearchSource[]
}

/**
 * 마크다운 콘텐츠를 렌더링하는 재사용 가능한 컴포넌트
 * 
 * @param content - 렌더링할 마크다운 텍스트
 * @param className - 추가 CSS 클래스
 * @param sources - 출처 목록 (인용 링크 연결용)
 */
export default function MarkdownContent({ content, className, sources }: MarkdownContentProps) {
  if (!content || !content.trim()) {
    return null
  }

  // [1], [2] 등을 링크로 변환하는 전처리
  const processedContent = React.useMemo(() => {
    if (!sources || sources.length === 0) return content;

    return content.replace(/\[(\d+)\]/g, (match, num) => {
      const index = parseInt(num) - 1;
      if (index >= 0 && index < sources.length) {
        // 마크다운 링크 문법으로 변환: [1](http://...)
        // 텍스트는 그대로 [1] 유지하고 링크만 겁니다.
        return `[${match}](${sources[index].url})`;
      }
      return match;
    });
  }, [content, sources]);

  return (
    <div className={cn("markdown-content prose dark:prose-invert max-w-none break-words", className)}>
      <ReactMarkdown
        components={{
          a: ({ node, ...props }) => (
            <a 
              {...props} 
              target="_blank" 
              rel="noopener noreferrer" 
              className="text-primary hover:underline font-medium decoration-primary/30 underline-offset-2 transition-colors"
            />
          ),
          // 인용구 스타일 개선
          blockquote: ({ node, ...props }) => (
            <blockquote 
              {...props} 
              className="border-l-4 border-primary/20 pl-4 py-1 my-4 bg-muted/30 rounded-r-lg italic"
            />
          )
        }}
      >
        {processedContent}
      </ReactMarkdown>
    </div>
  )
}

