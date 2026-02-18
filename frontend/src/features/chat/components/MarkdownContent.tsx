/**
 * 마크다운 콘텐츠 렌더링 컴포넌트
 * <think>...</think> 태그 내 추론 과정을 별도 UI로 표시
 */

import { useMemo, useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Brain, ChevronDown, ChevronUp, Loader2, Copy, Check } from 'lucide-react'
import { cn } from '@/shared/utils/cn'
import type { SearchSource } from '../types'

interface MarkdownContentProps {
  content: string
  className?: string
  sources?: SearchSource[]
  isStreaming?: boolean
}

function parseThinkingContent(content: string): {
  thinking: string | null
  mainContent: string
  isThinkingComplete: boolean
} {
  const thinkStart = content.indexOf('<think>')
  const thinkEnd = content.indexOf('</think>')

  if (thinkStart === -1) {
    return { thinking: null, mainContent: content, isThinkingComplete: true }
  }

  // <think> 태그가 있지만 </think>가 없는 경우 (스트리밍 중)
  if (thinkEnd === -1) {
    const thinking = content.slice(thinkStart + 7)
    // <think> 이전 내용은 무시 (일반적으로 없어야 함)
    return { thinking, mainContent: '', isThinkingComplete: false }
  }

  // <think>와 </think> 모두 있는 경우
  const thinking = content.slice(thinkStart + 7, thinkEnd)
  const mainContent = content.slice(thinkEnd + 8).trim()
  return { thinking, mainContent, isThinkingComplete: true }
}

function ThinkingBlock({
  content,
  isComplete,
  isStreaming,
}: {
  content: string
  isComplete: boolean
  isStreaming?: boolean
}) {
  const [isOpen, setIsOpen] = useState(true)

  // 추론이 완료되면 0.5초 후 자동으로 닫힘 (스트리밍이 끝났을 때만)
  useEffect(() => {
    if (isComplete && !isStreaming) {
      const timer = setTimeout(() => setIsOpen(false), 500)
      return () => clearTimeout(timer)
    }
  }, [isComplete, isStreaming])

  if (!content) return null

  return (
    <div
      className={cn(
        'mb-4 rounded-lg border overflow-hidden transition-all duration-300',
        isComplete
          ? 'bg-muted/20 border-border/50'
          : 'bg-purple-500/5 border-purple-500/30 shadow-[0_0_15px_rgba(168,85,247,0.1)]'
      )}
    >
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={cn(
          'flex items-center justify-between w-full px-4 py-2.5 text-sm font-medium transition-colors',
          isComplete
            ? 'text-muted-foreground hover:bg-muted/50'
            : 'text-purple-600 dark:text-purple-400 hover:bg-purple-500/10'
        )}
      >
        <div className="flex items-center gap-2">
          <div
            className={cn(
              'p-1.5 rounded-md transition-all',
              isComplete ? 'bg-muted/50' : 'bg-purple-500/20 animate-pulse'
            )}
          >
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

      <div
        className={cn(
          'overflow-hidden transition-all duration-300',
          isOpen ? 'max-h-[500px] opacity-100' : 'max-h-0 opacity-0'
        )}
      >
        <div
          className={cn(
            'px-4 py-3 text-sm border-t overflow-y-auto max-h-[400px]',
            isComplete
              ? 'bg-muted/10 border-border/30 text-muted-foreground'
              : 'bg-purple-500/5 border-purple-500/20 text-foreground/80'
          )}
        >
          <pre className="whitespace-pre-wrap font-sans leading-relaxed">{content}</pre>
        </div>
      </div>
    </div>
  )
}

function CodeBlock({ language, children }: { language: string; children: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(children)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="relative group my-4 rounded-lg overflow-hidden border border-border/50">
      <div className="flex items-center justify-between px-4 py-2 bg-zinc-800 text-zinc-400 text-xs">
        <span>{language || 'code'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1.5 hover:text-zinc-100 transition-colors"
        >
          {copied ? (
            <><Check className="h-3.5 w-3.5 text-green-400" /><span className="text-green-400">복사됨</span></>
          ) : (
            <><Copy className="h-3.5 w-3.5" /><span>복사</span></>
          )}
        </button>
      </div>
      <SyntaxHighlighter
        style={oneDark}
        language={language}
        PreTag="div"
        customStyle={{ margin: 0, borderRadius: 0, fontSize: '0.875rem' }}
      >
        {children}
      </SyntaxHighlighter>
    </div>
  )
}

export function MarkdownContent({
  content,
  className,
  sources,
  isStreaming,
}: MarkdownContentProps) {
  // 훅은 항상 같은 순서로 호출되어야 함 (조건부 early return 전에)
  const { thinking, mainContent, isThinkingComplete } = useMemo(
    () => parseThinkingContent(content || ''),
    [content]
  )

  const processedContent = useMemo(() => {
    if (!sources || sources.length === 0) return mainContent

    return mainContent.replace(/\[(\d+)\]/g, (match, num) => {
      const index = parseInt(num) - 1
      if (index >= 0 && index < sources.length) {
        return `[${match}](${sources[index].url})`
      }
      return match
    })
  }, [mainContent, sources])

  // 내용이 없으면 null 반환 (훅 호출 이후)
  if (!content || !content.trim()) {
    return null
  }

  return (
    <div className={cn('space-y-4', className)}>
      {thinking && (
        <ThinkingBlock
          content={thinking}
          isComplete={isThinkingComplete}
          isStreaming={isStreaming}
        />
      )}

      {mainContent && (
        <div className="markdown-content prose dark:prose-invert max-w-none break-words animate-in fade-in duration-300">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ ...props }) => (
                <a
                  {...props}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline font-medium decoration-primary/30 underline-offset-2 transition-colors"
                />
              ),
              blockquote: ({ ...props }) => (
                <blockquote
                  {...props}
                  className="border-l-4 border-primary/20 pl-4 py-1 my-4 bg-muted/30 rounded-r-lg italic"
                />
              ),
              code: ({ className, children, ...props }) => {
                const match = /language-(\w+)/.exec(className || '')
                const isInline = !match && !(props as { node?: { type?: string } }).node?.type?.includes('code')
                if (match) {
                  return (
                    <CodeBlock language={match[1]}>
                      {String(children).replace(/\n$/, '')}
                    </CodeBlock>
                  )
                }
                return (
                  <code
                    className={cn(
                      'bg-muted px-1.5 py-0.5 rounded text-sm font-mono',
                      isInline && 'text-foreground/90',
                      className
                    )}
                    {...props}
                  >
                    {children}
                  </code>
                )
              },
            }}
          >
            {processedContent}
          </ReactMarkdown>
        </div>
      )}
    </div>
  )
}
