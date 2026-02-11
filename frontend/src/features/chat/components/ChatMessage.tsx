/**
 * 개별 채팅 메시지 컴포넌트
 */

import { Globe, Database, MessageSquare, Brain, Sparkles, BookOpen, AlertCircle, RotateCcw, Loader2 } from 'lucide-react'
import { cn } from '@/shared/utils/cn'
import { type ChatMessage as ChatMessageType, type AIMode, AI_MODE_CONFIG, type MessageStatus } from '../types'
import { ThinkingProcess, ThinkingProcessAccordion } from './ThinkingDisplay'
import { SourceCarousel } from './SourceCarousel'
import { MarkdownContent } from './MarkdownContent'
import { QueryAnalysisDisplay } from './QueryAnalysisDisplay'
import { MessageActions } from './MessageActions'
import { Button } from '@/shared/components/ui/button'

function getModeIcon(mode?: AIMode) {
  switch (mode) {
    case 'search':
      return <Globe className="h-4 w-4" />
    case 'rag':
      return <Database className="h-4 w-4" />
    case 'reasoning':
      return <Brain className="h-4 w-4" />
    case 'hybrid':
      return <Sparkles className="h-4 w-4" />
    default:
      return <MessageSquare className="h-4 w-4" />
  }
}

interface ChatMessageProps {
  message: ChatMessageType
  threadId?: string
  onRetry?: () => void
  onRegenerate?: () => void
  isRegenerating?: boolean
}

/**
 * 메시지 상태에 따른 UI 컴포넌트
 */
function MessageStatusIndicator({
  status,
  onRetry,
}: {
  status?: MessageStatus
  onRetry?: () => void
}) {
  if (!status || status === 'completed') return null

  switch (status) {
    case 'generating':
      return (
        <div className="flex items-center gap-2 text-sm text-muted-foreground animate-pulse">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>응답 생성 중...</span>
        </div>
      )
    case 'failed':
      return (
        <div className="flex items-center gap-2 p-3 bg-destructive/10 border border-destructive/20 rounded-lg">
          <AlertCircle className="h-4 w-4 text-destructive" />
          <span className="text-sm text-destructive">응답 생성에 실패했습니다</span>
          {onRetry && (
            <Button
              variant="ghost"
              size="sm"
              onClick={onRetry}
              className="ml-auto text-destructive hover:text-destructive"
            >
              <RotateCcw className="h-4 w-4 mr-1" />
              재시도
            </Button>
          )}
        </div>
      )
    case 'cancelled':
      return (
        <div className="flex items-center gap-2 p-3 bg-muted border border-border rounded-lg">
          <AlertCircle className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm text-muted-foreground">응답이 취소되었습니다</span>
        </div>
      )
    case 'pending':
      return (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span>대기 중...</span>
        </div>
      )
    default:
      return null
  }
}

export function ChatMessage({ message, threadId, onRetry, onRegenerate, isRegenerating }: ChatMessageProps) {
  const { id, role, content, mode, sources, thinkingSteps, queryAnalysis, isStreaming, status } = message

  // 서버의 status 필드를 사용 (generating, completed, failed 등)
  const messageStatus = status as MessageStatus | undefined
  const isGenerating = messageStatus === 'generating' || isStreaming
  const isFailed = messageStatus === 'failed'
  const isCancelled = messageStatus === 'cancelled'

  return (
    <div
      className={cn(
        'flex gap-4 w-full group',
        role === 'user' ? 'justify-end' : 'justify-start'
      )}
    >
      <div
        className={cn(
          'w-full max-w-[90%]',
          role === 'user'
            ? 'px-5 py-3 bg-secondary text-secondary-foreground rounded-2xl rounded-tr-sm whitespace-pre-wrap ml-auto w-fit'
            : 'space-y-4'
        )}
      >
        {role === 'user' ? (
          <div className="flex items-center gap-2">
            <div className={cn('shrink-0', AI_MODE_CONFIG[mode || 'simple'].color)}>
              {getModeIcon(mode)}
            </div>
            <span className="text-base">{content}</span>
          </div>
        ) : (
          <>
            {/* 0. 메시지 상태 표시 (failed, cancelled, generating 등) */}
            {(isFailed || isCancelled) && (
              <MessageStatusIndicator status={messageStatus} onRetry={onRetry} />
            )}

            {/* 1. 진행 상태 (기존 ThinkingProcess - 스트리밍 중일 때만) */}
            {!isFailed && !isCancelled && status && (
              <ThinkingProcess status={status} mode={mode} />
            )}

            {/* 1.5. 쿼리 분석 결과 */}
            {queryAnalysis && <QueryAnalysisDisplay analysis={queryAnalysis} />}

            {/* 2. 사고 과정 */}
            {thinkingSteps && thinkingSteps.length > 0 && (
              <ThinkingProcessAccordion steps={thinkingSteps} isStreaming={isGenerating} />
            )}

            {/* 3. 검색 소스 */}
            {sources && sources.length > 0 && (
              <div className="space-y-3 animate-in fade-in slide-in-from-bottom-2 duration-500">
                <div className="flex items-center gap-2 text-sm font-semibold text-muted-foreground px-1">
                  <BookOpen className="h-4 w-4" />
                  <span>참조된 자료 ({sources.length})</span>
                </div>
                <SourceCarousel sources={sources} />
              </div>
            )}

            {/* 4. 답변 내용 */}
            {content && !isFailed && !isCancelled && (
              <div className="animate-in fade-in duration-300">
                <MarkdownContent
                  content={content}
                  sources={sources}
                  isStreaming={isGenerating}
                />
              </div>
            )}

            {/* 5. 내용 없이 생성 중인 경우 로딩 표시 */}
            {!content && isGenerating && !isFailed && (
              <MessageStatusIndicator status="generating" />
            )}

            {/* 6. 액션 버튼들 (복사, 좋아요, 재생성, 공유) - 완료된 메시지에만 */}
            {content && !isGenerating && !isFailed && !isCancelled && (
              <MessageActions
                content={content}
                messageId={id}
                threadId={threadId}
                onRegenerate={onRegenerate}
                isRegenerating={isRegenerating}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}
