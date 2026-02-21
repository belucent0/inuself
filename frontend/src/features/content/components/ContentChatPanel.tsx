/**
 * 콘텐츠 채팅 패널
 * ChatArea를 래핑하여 content_id 컨텍스트로 대화
 * + SourceContextPopover로 검색 범위 및 소스 선택
 */

import { useCallback, useState } from 'react'
import { ChatArea } from '@/features/chat/components/ChatArea'
import { useContentChat } from '@/shared/hooks/useContentChat'
import { SourceContextPopover } from './SourceContextPopover'
import type { SourceOptions } from './SourceContextPopover'
import type { ContentDetail, ContentSummary } from '../types'

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
    include_web_search: false,
    selected_content_ids: [content.id],
    include_all_docs: false,
  })

  const [additionalContents, setAdditionalContents] = useState<ContentSummary[]>([])

  const {
    messages,
    isStreaming,
    currentStreamingMessage,
    currentThinkingSteps,
    currentSources,
    sendMessage,
    regenerate,
  } = useContentChat(content.id, contentTitle, sourceOptions)

  const handleAddContent = useCallback(
    (c: ContentSummary) => {
      if (sourceOptions.selected_content_ids.includes(c.id)) return
      setSourceOptions((prev) => ({
        ...prev,
        selected_content_ids: [...prev.selected_content_ids, c.id],
      }))
      setAdditionalContents((prev) => {
        if (prev.find((x) => x.id === c.id)) return prev
        return [...prev, c]
      })
    },
    [sourceOptions.selected_content_ids]
  )

  const handleRemoveContent = useCallback(
    (id: string) => {
      if (id === content.id) return
      setSourceOptions((prev) => ({
        ...prev,
        selected_content_ids: prev.selected_content_ids.filter((cid) => cid !== id),
      }))
      setAdditionalContents((prev) => prev.filter((c) => c.id !== id))
    },
    [content.id]
  )

  const handleSendMessage = useCallback(
    (msg: string, mode?: string, model?: string) => {
      sendMessage(msg, mode, model)
    },
    [sendMessage]
  )

  const handleRegenerate = useCallback((mode?: string, model?: string) => {
    regenerate(mode, model)
  }, [regenerate])

  const sourceContextSlot = (
    <SourceContextPopover
      content={content}
      options={sourceOptions}
      onChange={setSourceOptions}
      fixedContentId={content.id}
      additionalContents={additionalContents}
      onAddContent={handleAddContent}
      onRemoveContent={handleRemoveContent}
      disabled={isStreaming}
    />
  )

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 min-h-0">
        <ChatArea
          messages={messages}
          isStreaming={isStreaming}
          currentStreamingMessage={currentStreamingMessage}
          currentThinkingSteps={currentThinkingSteps}
          currentSources={currentSources}
          onSendMessage={handleSendMessage}
          onRegenerate={handleRegenerate}
          sourceContextSlot={sourceContextSlot}
        />
      </div>
    </div>
  )
}
