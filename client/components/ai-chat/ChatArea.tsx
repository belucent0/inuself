"use client"

/**
 * Chat Area 컴포넌트
 *
 * SOLID 원칙:
 * - Single Responsibility: 메시지 표시와 입력만 담당
 * - Open/Closed: 새로운 메시지 타입 추가에 열려있음
 */

import { useEffect, useRef, useState } from 'react'
import { Message } from '@/lib/api/threads'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import {
  Bot,
  Send,
  ChevronDown,
  ExternalLink,
  Loader2,
  FileText,
  RefreshCw,
} from 'lucide-react'
import MarkdownContent from '@/components/MarkdownContent'
import { cn } from '@/lib/utils'

// 출처 모달 컴포넌트
function SourcesModal({ sources }: { sources: any[] }) {
  if (sources.length === 0) return null

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-7 px-2 text-xs gap-1"
        >
          <FileText className="h-3 w-3" />
          출처 {sources.length}개
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-md max-h-[70vh] overflow-hidden">
        <DialogHeader>
          <DialogTitle className="text-base">출처 ({sources.length}개)</DialogTitle>
        </DialogHeader>
        <div className="overflow-y-auto max-h-[50vh] space-y-2 pr-2">
          {sources.map((source, idx) => (
            <a
              key={idx}
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-start gap-2 p-2 rounded-lg bg-muted/50 hover:bg-muted transition-colors group"
            >
              <span className="flex-shrink-0 w-5 h-5 rounded bg-primary/10 text-primary text-xs flex items-center justify-center font-medium">
                {idx + 1}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate group-hover:text-primary transition-colors">
                  {source.title}
                </p>
                <p className="text-xs text-muted-foreground truncate">
                  {source.url}
                </p>
              </div>
              <ExternalLink className="h-3 w-3 flex-shrink-0 text-muted-foreground group-hover:text-primary transition-colors" />
            </a>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  )
}

// 스트리밍 중 출처 토글 (접혀있는 상태로 표시)
function StreamingSourcesToggle({ sources }: { sources: any[] }) {
  const [isOpen, setIsOpen] = useState(false)

  if (sources.length === 0) return null

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
        <ChevronDown className={cn('h-3 w-3 transition-transform', isOpen && 'rotate-180')} />
        <FileText className="h-3 w-3" />
        출처 수집 중... ({sources.length}개)
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1">
        <div className="space-y-1 pl-4">
          {sources.map((source, idx) => (
            <div key={idx} className="text-xs text-muted-foreground truncate">
              [{idx + 1}] {source.title}
            </div>
          ))}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

interface ChatAreaProps {
  messages: Message[]
  isStreaming: boolean
  currentStreamingMessage: string
  currentThinkingSteps?: any[]
  currentSources?: any[]
  onSendMessage: (content: string, mode?: string) => void
  onRegenerate?: () => void
}

function MessageItem({
  message,
  isLastInTurn,
  isLastAssistant,
  onRegenerate,
  isStreaming,
}: {
  message: Message
  isLastInTurn?: boolean
  isLastAssistant?: boolean
  onRegenerate?: () => void
  isStreaming?: boolean
}) {
  const isUser = message.role === 'user'

  const sources = message.metadata?.sources || []
  const mode = message.metadata?.mode

  // 사용자 메시지: 오른쪽 정렬 말풍선
  if (isUser) {
    return (
      <div className="flex justify-end pt-4 pb-2">
        <div className="max-w-[85%]">
          <div className="rounded-2xl rounded-tr-sm bg-primary px-3 py-2 text-primary-foreground">
            <p className="text-sm whitespace-pre-wrap">{message.content}</p>
          </div>
        </div>
      </div>
    )
  }

  // AI 메시지: 전체 너비
  return (
    <div className={cn("px-2 pt-1 pb-2", isLastInTurn && "pb-4 border-b border-border/30")}>
      {/* 모드 표시 */}
      {mode && (
        <div className="mb-1">
          <Badge variant="outline" className="text-xs">
            {mode}
          </Badge>
        </div>
      )}

      {/* AI 답변: Markdown 렌더링 - 전체 너비 사용 */}
      <div className="prose prose-sm dark:prose-invert max-w-full">
        <MarkdownContent content={message.content} />
      </div>

      {/* 하단 액션 버튼들 */}
      <div className="mt-3 flex items-center gap-2">
        {/* 출처 버튼 */}
        {sources.length > 0 && <SourcesModal sources={sources} />}

        {/* 재생성 버튼 - 마지막 AI 메시지에만 표시 */}
        {isLastAssistant && onRegenerate && !isStreaming && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs gap-1 text-muted-foreground hover:text-foreground"
            onClick={() => onRegenerate()}
          >
            <RefreshCw className="h-3 w-3" />
            재생성
          </Button>
        )}
      </div>
    </div>
  )
}

function StreamingMessage({
  content,
  thinkingSteps = [],
  sources = []
}: {
  content: string
  thinkingSteps?: any[]
  sources?: any[]
}) {
  const [isThinkingOpen, setIsThinkingOpen] = useState(true)
  const hasContent = content && content.trim().length > 0

  return (
    <div className="px-2 py-1">
      {/* 로딩 인디케이터 */}
      <div className="flex items-center gap-2 mb-1">
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        <span className="text-xs text-muted-foreground">답변 생성 중...</span>
      </div>

      {/* 실시간 사고 과정 - 토큰 생성 시작하면 숨김 */}
      {thinkingSteps.length > 0 && !hasContent && (
        <div className="transition-all duration-500 ease-out mb-2">
          <Collapsible open={isThinkingOpen} onOpenChange={setIsThinkingOpen}>
            <CollapsibleTrigger className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground transition-colors">
              <ChevronDown
                className={cn('h-4 w-4 transition-transform', isThinkingOpen && 'rotate-180')}
              />
              사고 과정 ({thinkingSteps.length}단계)
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-1">
              <Card className="p-2 space-y-1 bg-muted/30">
                {thinkingSteps.map((step, idx) => (
                  <div key={idx} className="text-xs">
                    <span className="font-semibold text-muted-foreground">{step.step}:</span>{' '}
                    <span>{step.content}</span>
                  </div>
                ))}
              </Card>
            </CollapsibleContent>
          </Collapsible>
        </div>
      )}

      {/* 실시간 소스 - 토글로 숨겨진 채 표시 */}
      {sources.length > 0 && (
        <div className="mb-2">
          <StreamingSourcesToggle sources={sources} />
        </div>
      )}

      {/* 스트리밍 답변 - 전체 너비 사용 */}
      {hasContent && (
        <div className="prose prose-sm dark:prose-invert max-w-full animate-in fade-in duration-300">
          <MarkdownContent content={content} />
        </div>
      )}
    </div>
  )
}

export function ChatArea({
  messages,
  isStreaming,
  currentStreamingMessage,
  currentThinkingSteps = [],
  currentSources = [],
  onSendMessage,
  onRegenerate,
}: ChatAreaProps) {
  const [inputValue, setInputValue] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // 스트리밍 중에는 하단으로 자동 스크롤 (답변이 길어질 때)
  useEffect(() => {
    if (isStreaming && currentStreamingMessage && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [currentStreamingMessage, isStreaming])

  const handleSubmit = () => {
    if (!inputValue.trim() || isStreaming) return

    onSendMessage(inputValue)
    setInputValue('')
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* 메시지 영역 */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {/* 중앙 정렬 래퍼 추가 */}
        <div className="max-w-3xl mx-auto px-4 py-6">
          {messages.length === 0 && !isStreaming && (
            <div className="flex flex-col items-center justify-center min-h-[calc(100vh-250px)] text-center text-muted-foreground">
              <Bot className="h-10 w-10 sm:h-12 sm:w-12 mb-3 opacity-50" />
              <p className="text-sm sm:text-base">대화를 시작해보세요</p>
            </div>
          )}

          {messages.map((message, idx) => {
          // 다음 메시지가 사용자 메시지이거나 마지막 메시지인 경우 턴의 마지막
          const nextMessage = messages[idx + 1]
          const isLastInTurn = message.role === 'assistant' && (!nextMessage || nextMessage.role === 'user')
          // 마지막 assistant 메시지인지 확인 (재생성 버튼 표시용)
          const isLastAssistant = message.role === 'assistant' && idx === messages.length - 1

          return (
            <div key={idx}>
              <MessageItem
                message={message}
                isLastInTurn={isLastInTurn}
                isLastAssistant={isLastAssistant}
                onRegenerate={onRegenerate}
                isStreaming={isStreaming}
              />
            </div>
          )
        })}

          {isStreaming && (
            <StreamingMessage
              content={currentStreamingMessage}
              thinkingSteps={currentThinkingSteps}
              sources={currentSources}
            />
          )}
        </div>
      </div>

      {/* 입력 영역 - sticky로 항상 하단에 표시 (사이드바 고려) */}
      <div className="sticky bottom-0 z-50 bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="max-w-3xl mx-auto px-4 py-4">
          <div className="relative rounded-3xl border border-input bg-background shadow-lg">
            <Textarea
              ref={textareaRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="추가 질문을 입력하세요..."
              className="min-h-[52px] max-h-[160px] resize-none border-0 bg-transparent px-4 py-3 pr-12 text-sm focus-visible:ring-0 focus-visible:ring-offset-0"
              disabled={isStreaming}
            />
            <Button
              onClick={handleSubmit}
              disabled={!inputValue.trim() || isStreaming}
              size="icon"
              className="absolute right-2 bottom-2 h-8 w-8 rounded-full"
            >
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
