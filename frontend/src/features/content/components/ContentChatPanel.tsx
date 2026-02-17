/**
 * 콘텐츠 채팅 패널
 * ChatArea를 래핑하여 content_id 컨텍스트로 대화
 */

import { useCallback } from 'react'
import { ChatArea } from '@/features/chat/components/ChatArea'
import { useContentChat } from '@/shared/hooks/useContentChat'

interface ContentChatPanelProps {
  contentId: string
  contentTitle: string
}

export function ContentChatPanel({
  contentId,
  contentTitle,
}: ContentChatPanelProps) {
  const {
    messages,
    isStreaming,
    currentStreamingMessage,
    currentThinkingSteps,
    currentSources,
    sendMessage,
    regenerate,
  } = useContentChat(contentId, contentTitle)

  const handleSendMessage = useCallback(
    (content: string, mode?: string, model?: string) => {
      sendMessage(content, mode, model)
    },
    [sendMessage]
  )

  const handleRegenerate = useCallback((mode?: string, model?: string) => {
    regenerate(mode, model)
  }, [regenerate])

  return (
    <div className="h-full flex flex-col">
      <ChatArea
        messages={messages}
        isStreaming={isStreaming}
        currentStreamingMessage={currentStreamingMessage}
        currentThinkingSteps={currentThinkingSteps}
        currentSources={currentSources}
        onSendMessage={handleSendMessage}
        onRegenerate={handleRegenerate}
      />
    </div>
  )
}
