/**
 * 메시지 목록 컴포넌트
 */

import { useRef, useEffect, useCallback } from 'react'
import { ScrollArea } from '@/shared/components/ui/scroll-area'
import { cn } from '@/shared/utils/cn'
import { type ChatMessage as ChatMessageType, type AIMode, AI_MODE_CONFIG } from '../types'
import { ChatMessage } from './ChatMessage'
import { ModeIcon } from './AIModeSelector'

interface MessageListProps {
  messages: ChatMessageType[]
  mode: AIMode
  threadId?: string
  onRegenerate?: () => void
  isRegenerating?: boolean
}

export function MessageList({ messages, mode, threadId, onRegenerate, isRegenerating }: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const isUserScrollingRef = useRef(false)
  const lastScrollTopRef = useRef(0)
  const prevMessageCountRef = useRef(0)

  const isNearBottom = useCallback(() => {
    const viewport = scrollAreaRef.current?.querySelector(
      '[data-radix-scroll-area-viewport]'
    ) as HTMLElement
    if (!viewport) return true
    const { scrollTop, scrollHeight, clientHeight } = viewport
    return scrollHeight - scrollTop - clientHeight < 100
  }, [])

  const handleScroll = useCallback(() => {
    const viewport = scrollAreaRef.current?.querySelector(
      '[data-radix-scroll-area-viewport]'
    ) as HTMLElement
    if (!viewport) return

    const currentScrollTop = viewport.scrollTop
    if (currentScrollTop < lastScrollTopRef.current - 10) {
      isUserScrollingRef.current = true
    }
    if (isNearBottom()) {
      isUserScrollingRef.current = false
    }
    lastScrollTopRef.current = currentScrollTop
  }, [isNearBottom])

  useEffect(() => {
    const viewport = scrollAreaRef.current?.querySelector(
      '[data-radix-scroll-area-viewport]'
    ) as HTMLElement
    if (viewport) {
      viewport.addEventListener('scroll', handleScroll, { passive: true })
      return () => viewport.removeEventListener('scroll', handleScroll)
    }
  }, [handleScroll])

  // 새 메시지 추가 시에만 자동 스크롤 (사용자가 맨 아래에 있을 때만)
  useEffect(() => {
    if (messages.length > prevMessageCountRef.current) {
      // 새 메시지 추가 시, 사용자가 맨 아래 근처에 있었으면 자동 스크롤
      if (isNearBottom()) {
        isUserScrollingRef.current = false
        scrollRef.current?.scrollIntoView({ behavior: 'smooth' })
      }
    }
    prevMessageCountRef.current = messages.length
  }, [messages.length, isNearBottom])

  // 스트리밍 중 스크롤: 사용자가 직접 위로 스크롤하면 자동 스크롤 비활성화
  // 사용자가 맨 아래로 돌아오면 다시 활성화
  useEffect(() => {
    // 스트리밍 중이 아니면 스킵
    const isStreaming = messages.some(m => m.isStreaming)
    if (!isStreaming) return

    // 사용자가 스크롤 중이면 강제 스크롤하지 않음
    if (isUserScrollingRef.current) return

    // 맨 아래에 있을 때만 자동 스크롤
    if (scrollRef.current && isNearBottom()) {
      scrollRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [
    messages[messages.length - 1]?.content,
    isNearBottom,
  ])

  if (messages.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-8 mt-20 opacity-50 space-y-4 animate-in fade-in zoom-in-95 duration-500">
        <div className={cn('p-4 rounded-full mb-4', AI_MODE_CONFIG[mode].bgColor)}>
          <div className={cn('h-12 w-12', AI_MODE_CONFIG[mode].color)}>
            <ModeIcon mode={mode} />
          </div>
        </div>
        <h2 className="text-3xl font-bold bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 bg-clip-text text-transparent">
          {AI_MODE_CONFIG[mode].label} 모드
        </h2>
        <p className="text-muted-foreground text-sm">{AI_MODE_CONFIG[mode].description}</p>
      </div>
    )
  }

  return (
    <ScrollArea className="flex-1 px-4" ref={scrollAreaRef}>
      <div className="flex flex-col gap-6 max-w-3xl mx-auto py-8">
        {messages.map((msg, idx) => {
          // 마지막 assistant 메시지에만 재생성 버튼 활성화
          const isLastAssistant = msg.role === 'assistant' && idx === messages.length - 1
          return (
            <ChatMessage
              key={msg.id}
              message={msg}
              threadId={threadId}
              onRegenerate={isLastAssistant ? onRegenerate : undefined}
              isRegenerating={isLastAssistant ? isRegenerating : undefined}
            />
          )
        })}
        {/* 동적 플레이스홀더: 화면 크기에 따라 하단 여백을 조절하여 마지막 메시지가 화면 중앙에 오도록 함 (스트리밍 중일 때만) */}
        {messages.some(m => m.isStreaming) && (
          <div className="min-h-[200px] h-[25vh] md:h-[40vh] lg:h-[50vh] transition-all duration-300 ease-out" aria-hidden="true" />
        )}
        <div ref={scrollRef} />
      </div>
    </ScrollArea>
  )
}
