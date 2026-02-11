/**
 * 큐에 대기 중인 메시지 표시 컴포넌트
 *
 * AI 응답 생성 중에 보낸 메시지는 큐에 대기하며,
 * 입력창 위에 비활성화된 스타일로 표시됩니다.
 */

import { Clock } from 'lucide-react'
import { cn } from '@/shared/utils/cn'
import { type AIMode, AI_MODE_CONFIG } from '../types'

interface QueuedMessageProps {
  content: string
  mode?: AIMode
  position: number  // 큐에서의 순서 (1부터 시작)
}

export function QueuedMessage({ content, mode = 'search', position }: QueuedMessageProps) {
  return (
    <div className="flex justify-end animate-in slide-in-from-bottom-2 duration-300">
      <div
        className={cn(
          'max-w-[85%] px-4 py-2.5 rounded-2xl rounded-tr-sm',
          'bg-muted/50 text-muted-foreground',
          'border border-dashed border-muted-foreground/30',
          'opacity-70'
        )}
      >
        <div className="flex items-center gap-2">
          {/* 모드 아이콘 */}
          <div className={cn('shrink-0 opacity-50', AI_MODE_CONFIG[mode].color)}>
            <Clock className="h-3.5 w-3.5" />
          </div>

          {/* 메시지 내용 */}
          <span className="text-sm">{content}</span>

          {/* 대기 순서 표시 */}
          <span className="ml-2 text-xs text-muted-foreground/60">
            #{position} 대기 중
          </span>
        </div>
      </div>
    </div>
  )
}

interface QueuedMessageListProps {
  messages: Array<{
    id: string
    content: string
    mode?: AIMode
  }>
}

export function QueuedMessageList({ messages }: QueuedMessageListProps) {
  if (messages.length === 0) return null

  return (
    <div className="space-y-2 px-4 py-2 bg-muted/20 border-t border-dashed border-muted-foreground/20">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Clock className="h-3 w-3" />
        <span>대기 중인 메시지</span>
      </div>
      {messages.map((msg, idx) => (
        <QueuedMessage
          key={msg.id}
          content={msg.content}
          mode={msg.mode}
          position={idx + 1}
        />
      ))}
    </div>
  )
}
