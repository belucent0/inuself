/**
 * 개별 채팅 메시지 컴포넌트
 */

import { Globe, Database, MessageSquare, Brain, Sparkles, BookOpen } from 'lucide-react'
import { cn } from '@/shared/utils/cn'
import { type ChatMessage as ChatMessageType, type AIMode, AI_MODE_CONFIG } from '../types'
import { ThinkingProcess, ThinkingProcessAccordion } from './ThinkingDisplay'
import { SourceCarousel } from './SourceCarousel'
import { MarkdownContent } from './MarkdownContent'
import { QueryAnalysisDisplay } from './QueryAnalysisDisplay'

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
}

export function ChatMessage({ message }: ChatMessageProps) {
  const { role, content, mode, sources, thinkingSteps, queryAnalysis, isStreaming, status } = message

  return (
    <div
      className={cn(
        'flex gap-4 w-full',
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
            {/* 1. 진행 상태 */}
            {status && <ThinkingProcess status={status} mode={mode} />}

            {/* 1.5. 쿼리 분석 결과 */}
            {queryAnalysis && <QueryAnalysisDisplay analysis={queryAnalysis} />}

            {/* 2. 사고 과정 */}
            {thinkingSteps && thinkingSteps.length > 0 && (
              <ThinkingProcessAccordion steps={thinkingSteps} isStreaming={isStreaming} />
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
            {content && (
              <div className="animate-in fade-in duration-300">
                <MarkdownContent
                  content={content}
                  sources={sources}
                  isStreaming={isStreaming}
                />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
