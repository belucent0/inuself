/**
 * 콘텐츠 채팅 패널
 * ChatArea를 래핑하여 content_id 컨텍스트로 대화
 * + SourceOptionsPanel로 요약/전사/화자 소스 선택
 */

import { useCallback, useState } from 'react'
import { ChatArea } from '@/features/chat/components/ChatArea'
import { useContentChat } from '@/shared/hooks/useContentChat'
import { SourceOptionsPanel } from './SourceOptionsPanel'
import type { SourceOptions } from './SourceOptionsPanel'
import type { ContentDetail } from '../types'

interface ContentChatPanelProps {
  content: ContentDetail
  contentTitle: string
}

export function ContentChatPanel({
  content,
  contentTitle,
}: ContentChatPanelProps) {
  const [sourceOptions, setSourceOptions] = useState<SourceOptions>({
    include_summary: true,
    include_transcription: true,
    speaker_filter: null,
  })

  const {
    messages,
    isStreaming,
    currentStreamingMessage,
    currentThinkingSteps,
    currentSources,
    sendMessage,
    regenerate,
  } = useContentChat(content.id, contentTitle, sourceOptions)

  const handleSendMessage = useCallback(
    (msg: string, mode?: string, model?: string) => {
      sendMessage(msg, mode, model)
    },
    [sendMessage]
  )

  const handleRegenerate = useCallback((mode?: string, model?: string) => {
    regenerate(mode, model)
  }, [regenerate])

  return (
    <div className="h-full flex flex-col">
      <SourceOptionsPanel
        content={content}
        options={sourceOptions}
        onChange={setSourceOptions}
      />
      <div className="flex-1 min-h-0">
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
    </div>
  )
}
