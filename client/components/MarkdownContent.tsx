'use client'

import ReactMarkdown from 'react-markdown'
import * as React from 'react'
import { cn } from '@/lib/utils'
import { Brain, ChevronDown, ChevronUp, Loader2 } from 'lucide-react'

interface SearchSource {
  position: number
  title: string
  url: string
  snippet: string
  engine?: string
  source?: 'web' | 'rag'
}

type MarkdownContentProps = {
  content: string
  className?: string
  sources?: SearchSource[]
  isStreaming?: boolean
}

/**
 * <think> 태그 내용을 파싱하여 분리
 */
function parseThinkingContent(content: string): { thinking: string | null; mainContent: string; isThinkingComplete: boolean } {
  const thinkStart = content.indexOf('<think>')
  const thinkEnd = content.indexOf('</think>')

  if (thinkStart === -1) {
    // <think> 태그가 없으면 전체가 메인 콘텐츠
    return { thinking: null, mainContent: content, isThinkingComplete: true }
  }

  if (thinkEnd === -1) {
    // <think>는 있지만 </think>가 없으면 아직 추론 중
    const thinking = content.slice(thinkStart + 7) // '<think>' 이후 내용
    return { thinking, mainContent: '', isThinkingComplete: false }
  }

  // 둘 다 있으면 완료됨
  const thinking = content.slice(thinkStart + 7, thinkEnd)
  const mainContent = content.slice(thinkEnd + 8).trim() // '</think>' 이후 내용
  return { thinking, mainContent, isThinkingComplete: true }
}

/**
 * 추론 과정 표시 컴포넌트
 */
function ThinkingBlock({
  content,
  isComplete,
  isStreaming
}: {
  content: string
  isComplete: boolean
  isStreaming?: boolean
}) {
  const [isOpen, setIsOpen] = React.useState(true)

  // 추론이 완료되면 자동으로 접기
  React.useEffect(() => {
    if (isComplete && !isStreaming) {
      const timer = setTimeout(() => setIsOpen(false), 500)
      return () => clearTimeout(timer)
    }
  }, [isComplete, isStreaming])

  return (
    <div className={cn(
      "mb-4 rounded-lg border overflow-hidden transition-all duration-300",
      isComplete
        ? "bg-muted/20 border-border/50"
        : "bg-purple-500/5 border-purple-500/30 shadow-[0_0_15px_rgba(168,85,247,0.1)]"
    )}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          "flex items-center justify-between w-full px-4 py-2.5 text-sm font-medium transition-colors",
          isComplete
            ? "text-muted-foreground hover:bg-muted/50"
            : "text-purple-600 dark:text-purple-400 hover:bg-purple-500/10"
        )}
      >
        <div className="flex items-center gap-2">
          <div className={cn(
            "p-1.5 rounded-md transition-all",
            isComplete
              ? "bg-muted/50"
              : "bg-purple-500/20 animate-pulse"
          )}>
            {!isComplete && isStreaming ? (
              <Loader2 className="h-4 w-4 text-purple-500 animate-spin" />
            ) : (
              <Brain className="h-4 w-4 text-purple-500" />
            )}
          </div>
          <span>
            {isComplete ? '추론 완료' : '추론 중'}
            {!isComplete && <span className="animate-pulse ml-1">...</span>}
          </span>
        </div>
        {isOpen ? (
          <ChevronUp className="h-4 w-4 opacity-50" />
        ) : (
          <ChevronDown className="h-4 w-4 opacity-50" />
        )}
      </button>

      <div className={cn(
        "overflow-hidden transition-all duration-300",
        isOpen ? "max-h-[500px] opacity-100" : "max-h-0 opacity-0"
      )}>
        <div className={cn(
          "px-4 py-3 text-sm border-t overflow-y-auto max-h-[400px]",
          isComplete
            ? "bg-muted/10 border-border/30 text-muted-foreground"
            : "bg-purple-500/5 border-purple-500/20 text-foreground/80"
        )}>
          <pre className="whitespace-pre-wrap font-sans leading-relaxed">
            {content}
          </pre>
        </div>
      </div>
    </div>
  )
}

/**
 * 마크다운 콘텐츠를 렌더링하는 재사용 가능한 컴포넌트
 * <think>...</think> 태그 내 추론 과정을 별도 UI로 표시
 *
 * @param content - 렌더링할 마크다운 텍스트
 * @param className - 추가 CSS 클래스
 * @param sources - 출처 목록 (인용 링크 연결용)
 * @param isStreaming - 스트리밍 중 여부
 */
export default function MarkdownContent({ content, className, sources, isStreaming }: MarkdownContentProps) {
  if (!content || !content.trim()) {
    return null
  }

  // <think> 태그 파싱
  const { thinking, mainContent, isThinkingComplete } = React.useMemo(
    () => parseThinkingContent(content),
    [content]
  )

  // [1], [2] 등을 링크로 변환하는 전처리
  const processedContent = React.useMemo(() => {
    if (!sources || sources.length === 0) return mainContent;

    return mainContent.replace(/\[(\d+)\]/g, (match, num) => {
      const index = parseInt(num) - 1;
      if (index >= 0 && index < sources.length) {
        // 마크다운 링크 문법으로 변환: [1](http://...)
        return `[${match}](${sources[index].url})`;
      }
      return match;
    });
  }, [mainContent, sources]);

  return (
    <div className={cn("space-y-4", className)}>
      {/* 추론 과정 표시 */}
      {thinking && (
        <ThinkingBlock
          content={thinking}
          isComplete={isThinkingComplete}
          isStreaming={isStreaming}
        />
      )}

      {/* 메인 콘텐츠 */}
      {mainContent && (
        <div className="markdown-content prose dark:prose-invert max-w-none break-words animate-in fade-in duration-300">
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
      )}
    </div>
  )
}

