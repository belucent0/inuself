/**
 * ChatInterface 통합 컴포넌트
 *
 * 원본 538줄 → 분해 후 약 100줄
 * 책임: 전체 채팅 UI 통합 관리
 */

import { useState } from 'react'
import { useThreadChat } from '@/shared/hooks/useThreadChat'
import { type AIMode, type ChatMessage as ChatMessageType } from '../types'
import { AIModeSelector } from './AIModeSelector'
import { MessageList } from './MessageList'
import { ChatInput } from './ChatInput'

interface ChatInterfaceProps {
  threadId?: string
  initialMessages?: ChatMessageType[]
}

export function ChatInterface({
  threadId: initialThreadId,
  initialMessages = [],
}: ChatInterfaceProps) {
  const [mode, setMode] = useState<AIMode>('search')
  const [currentThreadId, setCurrentThreadId] = useState<string | null>(initialThreadId || null)

  const {
    messages,
    input,
    handleInputChange,
    isLoading,
    sendMessage,
    setMessages,
    isStreaming,
    currentStreamingMessage,
    currentThinkingSteps,
    currentSources,
  } = useThreadChat({
    threadId: currentThreadId || 'new',
    initialMessages: initialMessages.map((m) => ({
      role: m.role,
      content: m.content,
      timestamp: Date.now() / 1000,
      metadata: {
        sources: m.sources,
        thinking_steps: m.thinkingSteps,
        mode: m.mode,
      },
    })),
  })

  const handleSendMessage = async (text: string) => {
    if (!text.trim() || isLoading) return
    await sendMessage(text, mode)
  }

  const handleModeChange = (newMode: AIMode) => {
    setMode(newMode)
  }

  const handleNewChat = () => {
    setMessages([])
    setCurrentThreadId(null)
  }

  // 메시지를 ChatMessage 타입으로 변환
  const chatMessages: ChatMessageType[] = messages.map((m, i) => {
    // 스트리밍 중인 마지막 메시지인 경우 실시간 데이터 사용
    const isLastMessage = i === messages.length - 1
    const isStreamingMessage = isStreaming && isLastMessage && m.role === 'assistant'

    return {
      id: `${m.timestamp}-${i}`,
      role: m.role,
      content: isStreamingMessage ? currentStreamingMessage : m.content,
      mode: (m.metadata?.mode as AIMode) || mode,
      sources: (isStreamingMessage ? currentSources : m.metadata?.sources)?.map((s, idx) => ({
        position: idx + 1,
        title: s.title,
        url: s.url,
        snippet: s.snippet,
        engine: s.engine,
        source: s.source,
      })),
      thinkingSteps: isStreamingMessage ? currentThinkingSteps : m.metadata?.thinking_steps,
      isStreaming: isStreamingMessage,
      status: isStreamingMessage ? '답변 생성 중...' : undefined,
    }
  })

  return (
    <div className="flex flex-col h-[calc(85vh-4rem)] md:h-[calc(100vh-4rem)] relative">
      {/* 헤더: 모드 선택 */}
      <div className="flex items-center justify-between px-4 py-2 border-b bg-background/80 backdrop-blur-sm">
        <AIModeSelector mode={mode} onModeChange={handleModeChange} disabled={isLoading} />
        {currentThreadId && (
          <button
            onClick={handleNewChat}
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            새 대화
          </button>
        )}
      </div>

      {/* 메시지 목록 */}
      <MessageList messages={chatMessages} mode={mode} />

      {/* 입력 영역 */}
      <div className="absolute bottom-0 left-0 right-0 p-4 bg-background/80 backdrop-blur-sm z-10 transition-all">
        <ChatInput
          input={input}
          onInputChange={(value) =>
            handleInputChange({ target: { value } } as React.ChangeEvent<HTMLTextAreaElement>)
          }
          onSendMessage={handleSendMessage}
          isLoading={isLoading}
          mode={mode}
          onModeChange={handleModeChange}
          showModeDescription={chatMessages.length === 0}
        />
      </div>
    </div>
  )
}
