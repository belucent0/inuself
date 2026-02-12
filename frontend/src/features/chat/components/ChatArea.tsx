/**
 * Chat Area 컴포넌트
 *
 * SOLID 원칙:
 * - Single Responsibility: 메시지 표시와 입력만 담당
 * - Open/Closed: 새로운 메시지 타입 추가에 열려있음
 */

import { useEffect, useRef, useState } from 'react'
import { Button } from '@/shared/components/ui/button'
import { Textarea } from '@/shared/components/ui/textarea'
import { Badge } from '@/shared/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/components/ui/collapsible'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/shared/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import {
  Bot,
  Send,
  ChevronDown,
  ExternalLink,
  Loader2,
  FileText,
  RefreshCw,
} from 'lucide-react'
import { MarkdownContent } from './MarkdownContent'
import { ThinkingProcessAccordion } from './ThinkingDisplay'
import { cn } from '@/shared/utils/cn'
import type { SearchSource, ThinkingStep, AIMode } from '../types'
import { AI_MODE_CONFIG } from '../types'

// Message 인터페이스
// v1.0.0: 상태 세분화 추가
type MessageStatus = 'queued' | 'analyzing' | 'searching' | 'thinking' | 'generating' | 'completed' | 'failed' | 'pending' | 'cancelled'

interface Message {
  role: 'user' | 'assistant'
  content: string
  timestamp?: number
  status?: MessageStatus  // v1.0.0: 상태 세분화
  metadata?: {
    sources?: SearchSource[]
    thinking_steps?: ThinkingStep[]
    mode?: AIMode
  }
}

// v1.0.0: 상태별 UI 설정
const MESSAGE_STATUS_CONFIG: Record<string, { icon: typeof Loader2; text: string }> = {
  queued: { icon: Loader2, text: '대기 중...' },
  analyzing: { icon: Loader2, text: '질문 분석 중...' },
  searching: { icon: Loader2, text: '검색 중...' },
  thinking: { icon: Loader2, text: '생각 중...' },
  generating: { icon: Loader2, text: '답변 작성 중...' },
}

// 출처 모달 컴포넌트
function SourcesModal({ sources }: { sources: SearchSource[] }) {
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
function StreamingSourcesToggle({ sources, hasContent }: { sources: SearchSource[]; hasContent: boolean }) {
  const [isOpen, setIsOpen] = useState(false)

  if (sources.length === 0) return null

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
        <ChevronDown className={cn('h-3 w-3 transition-transform', isOpen && 'rotate-180')} />
        <FileText className="h-3 w-3" />
        {!hasContent ? `${sources.length}개 문서로 답변 준비 중...` : '답변 중...'}
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
  currentThinkingSteps?: ThinkingStep[]
  currentSources?: SearchSource[]
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
  const thinkingSteps = message.metadata?.thinking_steps || []
  const mode = message.metadata?.mode

  // v1.0.0: AI 응답 대기/생성 중 상태 감지
  const isProcessing = ['queued', 'analyzing', 'searching', 'thinking', 'generating'].includes(message.status || '')
  const statusConfig = message.status ? MESSAGE_STATUS_CONFIG[message.status] : null

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

  // v1.0.0: AI 응답 처리 중 상태 (content 없음)
  if (isProcessing && !message.content) {
    return (
      <div className="px-2 py-4">
        {/* 모드 표시 */}
        {mode && (
          <div className="mb-2">
            <Badge variant="outline" className="text-xs">
              {mode}
            </Badge>
          </div>
        )}

        {/* thinking_steps 표시 (DB에서 로드됨) */}
        {thinkingSteps.length > 0 && (
          <ThinkingProcessAccordion steps={thinkingSteps} isStreaming={true} />
        )}

        {/* sources 표시 (DB에서 로드됨) */}
        {sources.length > 0 && (
          <div className="mb-2">
            <StreamingSourcesToggle sources={sources} hasContent={false} />
          </div>
        )}

        {/* v1.0.0: 상태별 로딩 인디케이터 */}
        <div className="flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          <span className="text-sm text-muted-foreground">
            {statusConfig?.text || '답변 생성 중...'}
          </span>
        </div>

        {/* 스켈레톤 UI - thinking_steps도 sources도 없을 때만 표시 */}
        {thinkingSteps.length === 0 && sources.length === 0 && (
          <div className="mt-3 space-y-2 opacity-50">
            <div className="h-4 bg-muted/50 rounded w-3/4 animate-pulse" />
            <div className="h-4 bg-muted/50 rounded w-1/2 animate-pulse" />
          </div>
        )}
      </div>
    )
  }

  // AI 메시지: 전체 너비
  return (
    <div className={cn("px-2 pt-1 pb-2", isLastInTurn && "pb-4 border-b border-border/30")}>
      {/* 모드 표시 */}
      {mode && (
        <div className="mb-2">
          <Badge variant="outline" className="text-xs">
            {mode}
          </Badge>
        </div>
      )}

      {/* 사고 과정 아코디언 - 모드 아래, 답변 위에 표시 */}
      {thinkingSteps.length > 0 && (
        <ThinkingProcessAccordion steps={thinkingSteps} isStreaming={false} />
      )}

      {/* AI 답변: Markdown 렌더링 - 전체 너비 사용 */}
      <div className="prose prose-sm dark:prose-invert max-w-full">
        <MarkdownContent content={message.content} sources={sources} />
      </div>

      {/* 하단 액션 버튼들 */}
      <div className="mt-3 flex items-center gap-2">
        {/* 출처 버튼 */}
        {sources.length > 0 && <SourcesModal sources={sources} />}

        {/* F-3: 재생성 버튼 - 마지막 AI 메시지에만 표시 (processing 중이 아닐 때만) */}
        {isLastAssistant && onRegenerate && !isStreaming && !isProcessing && (
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
  thinkingSteps?: ThinkingStep[]
  sources?: SearchSource[]
}) {
  const hasContent = content && content.trim().length > 0

  return (
    <div className="px-2 py-1 animate-in fade-in duration-300">
      {/* 로딩 인디케이터 - 답변 생성 전에만 표시 */}
      {!hasContent && thinkingSteps.length === 0 && (
        <div className="flex flex-col gap-4 py-4">
          <div className="flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            <span className="text-xs text-muted-foreground">답변 생성 중...</span>
          </div>
          {/* 스켈레톤 UI 효과 (선택적) */}
          <div className="space-y-2 opacity-50">
            <div className="h-4 bg-muted/50 rounded w-3/4 animate-pulse" />
            <div className="h-4 bg-muted/50 rounded w-1/2 animate-pulse" />
          </div>
        </div>
      )}

      {/* 실시간 사고 과정 - ThinkingProcessAccordion 사용 */}
      {thinkingSteps.length > 0 && (
        <ThinkingProcessAccordion steps={thinkingSteps} isStreaming={true} />
      )}

      {/* 실시간 소스 - 토글로 숨겨진 채 표시 */}
      {sources.length > 0 && (
        <div className="mb-2">
          <StreamingSourcesToggle sources={sources} hasContent={!!hasContent} />
        </div>
      )}

      {/* 스트리밍 답변 - 전체 너비 사용 */}
      {hasContent && (
        <div className="prose prose-sm dark:prose-invert max-w-full animate-in fade-in duration-300">
          <MarkdownContent content={content} sources={sources} isStreaming />
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
  const [mode, setMode] = useState<AIMode>('hybrid')
  const [showScrollBottom, setShowScrollBottom] = useState(false)
  const [inputHeight, setInputHeight] = useState(0)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const inputContainerRef = useRef<HTMLDivElement>(null)

  // 사용자 스크롤 의도 감지 - 스트리밍 중 자동 스크롤 차단 여부 결정
  const userHasScrolledUpRef = useRef(false)
  const lastScrollTopRef = useRef(0)

  // 스크롤 이벤트 핸들러 - 사용자 스크롤 의도 감지
  const handleScroll = () => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current

    const isAtBottom = scrollHeight - scrollTop - clientHeight < 100
    setShowScrollBottom(!isAtBottom)

    // 사용자가 위로 스크롤했는지 감지 (10px 이상 위로 이동)
    if (scrollTop < lastScrollTopRef.current - 10) {
      userHasScrolledUpRef.current = true
    }

    // 하단 근처로 돌아오면 자동 스크롤 재활성화
    if (isAtBottom) {
      userHasScrolledUpRef.current = false
    }

    lastScrollTopRef.current = scrollTop
  }

  // 하단으로 스크롤 이동
  const scrollToBottom = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: 'smooth'
      })
      userHasScrolledUpRef.current = false
      setShowScrollBottom(false)
    }
  }

  // 스트리밍 시작 시 한 번만 하단으로 스크롤 (사용자 메시지 전송 직후)
  const prevStreamingRef = useRef(false)
  useEffect(() => {
    if (isStreaming && !prevStreamingRef.current && scrollRef.current) {
      // 스트리밍 시작 시 사용자 스크롤 상태 초기화 & 하단으로 이동
      userHasScrolledUpRef.current = false
      requestAnimationFrame(() => {
        if (scrollRef.current) {
          scrollRef.current.scrollTop = scrollRef.current.scrollHeight
          lastScrollTopRef.current = scrollRef.current.scrollTop
        }
      })
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming])

  // 스트리밍 중 자동 스크롤 - 사용자가 위로 스크롤하지 않았을 때만
  useEffect(() => {
    // 사용자가 위로 스크롤했으면 자동 스크롤하지 않음
    if (userHasScrolledUpRef.current) return

    if (isStreaming && scrollRef.current) {
      requestAnimationFrame(() => {
        if (scrollRef.current && !userHasScrolledUpRef.current) {
          scrollRef.current.scrollTop = scrollRef.current.scrollHeight
          lastScrollTopRef.current = scrollRef.current.scrollTop
        }
      })
    }
  }, [currentStreamingMessage, isStreaming, currentThinkingSteps])

  // 사용자 메시지 전송 시 하단으로 스크롤
  useEffect(() => {
    if (inputContainerRef.current) {
      setInputHeight(inputContainerRef.current.offsetHeight)
    }

    if (messages.length > 0 && scrollRef.current) {
      const lastMessage = messages[messages.length - 1]

      if (lastMessage.role === 'user') {
        requestAnimationFrame(() => {
          if (scrollRef.current) {
            scrollRef.current.scrollTo({
              top: scrollRef.current.scrollHeight,
              behavior: 'smooth'
            })
          }
        })
      }
      // 히스토리 로딩 시: 자동 스크롤하지 않고 플로팅 버튼 표시
      else if (lastMessage.role === 'assistant' && !isStreaming) {
        // DOM 렌더링 후 스크롤 위치에 따라 플로팅 버튼 표시 여부 결정
        requestAnimationFrame(() => {
          if (scrollRef.current) {
            const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
            const isAtBottom = scrollHeight - scrollTop - clientHeight < 100
            setShowScrollBottom(!isAtBottom)
          }
        })
      }
    }
  }, [messages.length, isStreaming])

  // textarea 동적 높이 조절
  useEffect(() => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = 'auto'
      const newHeight = Math.min(textarea.scrollHeight, 160) // 최대 160px
      textarea.style.height = `${newHeight}px`
    }
  }, [inputValue])

  // 입력 영역 전체 높이 감지 (ResizeObserver + 상태 변화)
  useEffect(() => {
    const observeInputHeight = () => {
      if (inputContainerRef.current) {
        const height = inputContainerRef.current.offsetHeight
        setInputHeight(height)
      }
    }

    observeInputHeight()
    const resizeObserver = new ResizeObserver(observeInputHeight)
    if (inputContainerRef.current) {
      resizeObserver.observe(inputContainerRef.current)
    }

    return () => resizeObserver.disconnect()
  }, [])

  // 모든 상태 변화 시 높이 재계산 (mode, isStreaming 등)
  useEffect(() => {
    if (inputContainerRef.current) {
      const height = inputContainerRef.current.offsetHeight
      setInputHeight(height)
    }
  }, [mode, isStreaming])

  const handleSubmit = () => {
    if (!inputValue.trim() || isStreaming) return

    onSendMessage(inputValue, mode)
    setInputValue('')
    setShowScrollBottom(false)

    // 전송 후 높이 리셋
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }

    // 입력 후 스크롤 처리는 useEffect에서 담당
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const availableModes: AIMode[] = ['search', 'rag', 'reasoning', 'hybrid']

  return (
    <div className="flex flex-col h-full relative">
      {/* 메시지 영역 - 입력창 높이 + 여유 padding */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto scroll-smooth"
        style={{ paddingBottom: `calc(${inputHeight}px + 2rem)` }}
      >
        {/* 중앙 정렬 래퍼 추가 */}
        <div className={cn(
          "max-w-3xl mx-auto px-4 py-6",
          messages.length === 0 && !isStreaming && "flex items-center justify-center h-full"
        )}>
          {messages.length === 0 && !isStreaming && (
            <div className="flex flex-col items-center justify-center text-center text-muted-foreground">
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

          {/* 동적 플레이스홀더: 스트리밍 중에만 표시 */}
          {isStreaming && (
            <div className="min-h-[9vh] sm:min-h-[12vh]" aria-hidden="true" />
          )}
        </div>
      </div>

      {/* 프롬프트 입력 영역 - sticky로 하단 고정 (fixed보다 레이아웃 안정적) */}
      <div
        ref={inputContainerRef}
        className="sticky bottom-0 z-50 bg-transparent backdrop-blur-sm"
      >
        <div className="max-w-3xl mx-auto w-full px-4 py-4">
          <div className="relative rounded-3xl border border-input bg-background shadow-lg">
            {/* 입력창 내부 상단 영역 - 모드 선택 드롭다운 */}
            <div className="flex items-center gap-2 px-4 pt-3 pb-1">
              <DropdownMenu>
                <DropdownMenuTrigger asChild disabled={isStreaming}>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 px-3 rounded-full text-xs font-medium gap-1 text-muted-foreground hover:text-foreground hover:bg-muted/50"
                  >
                    <span className={cn('w-2 h-2 rounded-full',
                      mode === 'search' && 'bg-blue-500',
                      mode === 'rag' && 'bg-green-500',
                      mode === 'reasoning' && 'bg-purple-500',
                      mode === 'hybrid' && 'bg-amber-500',
                      mode === 'simple' && 'bg-slate-400'
                    )} />
                    {AI_MODE_CONFIG[mode].label}
                    <ChevronDown className="h-3 w-3 opacity-50" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent side="top" align="start" className="min-w-[140px]">
                  {availableModes.map((m) => {
                    const config = AI_MODE_CONFIG[m]
                    return (
                      <DropdownMenuItem
                        key={m}
                        onClick={() => setMode(m)}
                        className={cn(
                          'cursor-pointer gap-2',
                          mode === m && 'bg-accent'
                        )}
                      >
                        <span className={cn('w-2 h-2 rounded-full',
                          m === 'search' && 'bg-blue-500',
                          m === 'rag' && 'bg-green-500',
                          m === 'reasoning' && 'bg-purple-500',
                          m === 'hybrid' && 'bg-amber-500',
                          m === 'simple' && 'bg-slate-400'
                        )} />
                        {config.label}
                      </DropdownMenuItem>
                    )
                  })}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            <Textarea
              ref={textareaRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="추가 질문을 입력하세요..."
              className="min-h-[60px] max-h-[120px] resize-none border-0 bg-transparent px-4 py-2 pr-12 text-sm focus-visible:ring-0 focus-visible:ring-offset-0 overflow-y-auto"
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

      {/* 하단 스크롤 버튼 */}
      {showScrollBottom && (
        <Button
          onClick={scrollToBottom}
          size="icon"
          variant="secondary"
          className="absolute left-1/2 -translate-x-1/2 rounded-full shadow-lg opacity-90 hover:opacity-100 transition-opacity z-[60] w-8 h-8 bg-background/80 backdrop-blur-sm border"
          style={{ bottom: inputHeight + 16 }}
        >
          <ChevronDown className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}
