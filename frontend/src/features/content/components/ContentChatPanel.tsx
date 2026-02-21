/**
 * 콘텐츠 채팅 패널
 * ChatArea를 래핑하여 content_id 컨텍스트로 대화
 * + SourceContextPopover로 검색 범위 및 소스 선택
 * v1.1.0: 재방문 시 이전 스레드 자동 복원 + "새 대화" 버튼
 */

import { useCallback, useState } from 'react'
import { toast } from 'sonner'
import { ChatArea } from '@/features/chat/components/ChatArea'
import { useContentChat } from '@/shared/hooks/useContentChat'
import { SourceContextPopover } from './SourceContextPopover'
import { getContent } from '@/shared/services/endpoints/contents'
import type { SourceOptions } from './SourceContextPopover'
import type { ContentDetail, ContentSummary } from '../types'
import type { ContentSourceOptions } from '@/shared/hooks/useContentChat'

const MAX_SELECTED_DOCS = 5

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

  // metadata에서 복원된 source_options를 state에 반영 + additionalContents UI 복원
  const handleRestoreOptions = useCallback(async (restoredOptions: ContentSourceOptions) => {
    setSourceOptions(restoredOptions as SourceOptions)

    // 주 콘텐츠 제외한 추가 선택 콘텐츠 복원
    const additionalIds = (restoredOptions.selected_content_ids ?? []).filter(
      (id) => id !== content.id
    )
    if (additionalIds.length > 0) {
      const results = await Promise.allSettled(additionalIds.map((id) => getContent(id)))
      const restored: ContentSummary[] = results
        .filter(
          (r): r is PromiseFulfilledResult<ContentDetail> => r.status === 'fulfilled'
        )
        .map((r) => r.value)
      setAdditionalContents(restored)
    }
  }, [content.id])

  const {
    messages,
    isStreaming,
    isInitializing,
    currentStreamingMessage,
    currentThinkingSteps,
    currentSources,
    sendMessage,
    regenerate,
    startNewThread,
  } = useContentChat(content.id, contentTitle, sourceOptions, handleRestoreOptions)

  const handleAddContent = useCallback(
    (c: ContentSummary) => {
      if (sourceOptions.selected_content_ids.includes(c.id)) return
      if (sourceOptions.selected_content_ids.length >= MAX_SELECTED_DOCS) {
        toast.warning(`문서는 최대 ${MAX_SELECTED_DOCS}개까지 선택할 수 있습니다.`)
        return
      }
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
          isInitializing={isInitializing}
          currentStreamingMessage={currentStreamingMessage}
          currentThinkingSteps={currentThinkingSteps}
          currentSources={currentSources}
          onSendMessage={handleSendMessage}
          onRegenerate={handleRegenerate}
          onNewChat={startNewThread}
          sourceContextSlot={sourceContextSlot}
        />
      </div>
    </div>
  )
}
