/**
 * ChatPage - /chat/:threadId 라우트
 *
 * v1.0.0: 확인 후 라우팅 + SSE 재연결
 * - HomePage에서 POST로 스레드 생성 후 /chat/{threadId}?messageId={messageId}로 진입
 * - messageId가 있으면 SSE 스트리밍 연결
 * - 재연결 시 partial_content 복구
 */

import { useParams, useSearchParams, useNavigate } from 'react-router-dom'
import { useEffect, useCallback, useRef, useState } from 'react'
import { useThread } from '@/shared/hooks/useThreads'
import { useChatStore } from '@/shared/stores/chatStore'
import { useThreadTitle } from '@/shared/contexts/ThreadTitleContext'
import { threadsApi } from '@/shared/services'
import { ChatArea } from '@/features/chat/components/ChatArea'
import { ContentBanner } from '@/features/chat/components/ContentBanner'
import { toast } from 'sonner'
import { resumeMessageStream, sendMessageStream } from '@/shared/services/chatStreamService'

// v1.0.0: 메시지 상태 타입
type MessageStatus = 'queued' | 'analyzing' | 'searching' | 'thinking' | 'generating' | 'completed' | 'failed'

export function ChatPage() {
  const { threadId: paramThreadId } = useParams<{ threadId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()

  // v1.0.0: messageId 파라미터 (SSE 스트리밍 시작용)
  const messageId = searchParams.get('messageId')
  const mode = searchParams.get('mode') || 'auto'

  const threadId = paramThreadId || ''

  // TanStack Query로 스레드 로드 (기존 스레드일 때만)
  const { thread, isLoading: isThreadLoading } = useThread(threadId || null)

  // Zustand store
  const {
    threadId: storeThreadId,
    messages,
    streaming,
    switchThread,
    regenerate,
  } = useChatStore()

  const { setThreadTitle, registerHandlers, unregisterHandlers } = useThreadTitle()

  // ============================================================
  // 스레드 제목 관리
  // ============================================================
  useEffect(() => {
    if (thread?.title) {
      setThreadTitle(thread.title)
    } else {
      setThreadTitle('AI 채팅')
    }
  }, [thread?.title, setThreadTitle])

  // ============================================================
  // 편집/삭제 핸들러
  // ============================================================
  const handleEdit = useCallback(async () => {
    const newTitle = window.prompt('새 제목을 입력하세요', thread?.title || '')
    if (newTitle && newTitle.trim() && threadId) {
      try {
        await threadsApi.updateThreadTitle(threadId, newTitle.trim())
        setThreadTitle(newTitle.trim())
        toast.success('제목이 변경되었습니다')
      } catch {
        toast.error('제목 변경에 실패했습니다')
      }
    }
  }, [thread?.title, threadId, setThreadTitle])

  const handleDelete = useCallback(async () => {
    if (window.confirm('이 대화를 삭제하시겠습니까?') && threadId) {
      try {
        await threadsApi.deleteThread(threadId)
        toast.success('대화가 삭제되었습니다')
        switchThread(null)
        navigate('/')
      } catch {
        toast.error('대화 삭제에 실패했습니다')
      }
    }
  }, [threadId, navigate, switchThread])

  useEffect(() => {
    registerHandlers(handleEdit, handleDelete)
    return () => unregisterHandlers()
  }, [handleEdit, handleDelete, registerHandlers, unregisterHandlers])

  // ============================================================
  // 핵심: TanStack Query 데이터 → Zustand store 동기화
  // ============================================================
  useEffect(() => {
    // 로딩 중이면 스킵
    if (isThreadLoading) return

    // 스레드 데이터가 없거나 URL과 불일치하면 스킵
    if (!thread || thread.thread_id !== threadId) return

    // 현재 스트리밍 중인 같은 스레드면 덮어쓰지 않음
    if (streaming.isStreaming && storeThreadId === threadId) return

    // 이미 같은 스레드가 설정되어 있고 메시지가 있으면 스킵
    // v1.0.0: messageId가 있어도 store에 메시지가 없으면 로드 필요 (새로고침 케이스)
    if (storeThreadId === threadId && messages.length > 0) return

    // 메시지 포맷 변환 및 store에 설정
    const formattedMessages = thread.messages?.map((m) => ({
      message_id: m.message_id,
      role: m.role as 'user' | 'assistant',
      content: m.content,
      timestamp: m.timestamp,
      status: m.status,
      metadata: m.metadata ? {
        sources: m.metadata.sources,
        thinking_steps: m.metadata.thinking_steps,
        mode: m.metadata.mode as any,
      } : undefined,
    })) || []

    switchThread(threadId, formattedMessages)
  }, [thread, threadId, isThreadLoading, storeThreadId, messages.length, streaming.isStreaming, switchThread])

  // ============================================================
  // stuck 메시지 자동 재연결
  // messages가 로드된 후 in-progress 상태 메시지가 있으면 SSE 재연결 트리거
  // ============================================================
  useEffect(() => {
    if (!threadId || messageId || streaming.isStreaming) return

    const IN_PROGRESS_STATUSES = ['queued', 'analyzing', 'searching', 'thinking', 'generating']
    const lastAssistant = [...messages].reverse().find(m => m.role === 'assistant')
    if (lastAssistant?.message_id && IN_PROGRESS_STATUSES.includes(lastAssistant.status || '')) {
      setSearchParams(prev => {
        prev.set('messageId', lastAssistant.message_id!)
        return prev
      }, { replace: true })
    }
  }, [messages, threadId, messageId, streaming.isStreaming, setSearchParams])

  // ============================================================
  // v1.0.0: SSE 스트리밍 연결 (messageId가 있을 때)
  // ============================================================
  const connectedMessageIdRef = useRef<string | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [_messageStatus, setMessageStatus] = useState<MessageStatus | null>(null)

  // 콘텐츠 배너: 컨텍스트 ON/OFF
  const [contentContextEnabled, setContentContextEnabled] = useState(true)
  const threadContentId = thread?.content_id ?? null

  useEffect(() => {
    // messageId가 없거나 threadId가 없으면 스킵
    if (!messageId || !threadId) return

    // 이미 같은 messageId로 연결했으면 스킵
    if (connectedMessageIdRef.current === messageId) return
    connectedMessageIdRef.current = messageId

    console.log('[ChatPage v1.0.0] Connecting SSE:', { threadId, messageId })

    // 스트리밍 모드 시작 (UI 상태 업데이트)
    // 매번 getState()를 호출하여 최신 액션을 사용해야 React 리렌더링이 트리거됨
    const store = useChatStore.getState()
    store.startStreamingMode()
    const abortController = new AbortController()
    void resumeMessageStream(threadId, messageId, mode, {
      onToken: store.appendStreamingContent,
      onContent: store.setStreamingContent,
      onThinkingStep: store.addThinkingStep,
      onSource: () => {},
      onSources: store.setSources,
      onSearchQueries: () => {},
      onComplete: (message) => {
        store.finishStreaming(message.content, message.metadata, messageId)
        setMessageStatus('completed')
        setSearchParams((prev) => {
          prev.delete('messageId')
          return prev
        })
      },
      onError: (error) => {
        store.cancelStreaming()
        setMessageStatus('failed')
        toast.error(`Stream error: ${error.message}`)
      },
    }, abortController.signal).catch((error) => {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        console.error('[ChatPage v1.0.0] SSE connection failed:', error)
      }
    })

    // 클린업
    return () => {
      console.log('[ChatPage v1.0.0] Closing SSE connection')
      abortController.abort()
      connectedMessageIdRef.current = null
    }
  }, [messageId, mode, threadId, setSearchParams])

  // 페이지 떠날 때 SSE 연결 정리
  useEffect(() => {
    return () => {
      useChatStore.getState().cancelStreaming()
    }
  }, [])

  // ============================================================
  // 로딩 상태
  // ============================================================
  if (isThreadLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  // ============================================================
  // 이벤트 핸들러
  // ============================================================
  const handleSendMessage = async (content: string, msgMode?: string, model?: string) => {
    // v1.0.0: 두 번째 메시지도 확인 후 라우팅 흐름 사용
    if (!threadId || streaming.isStreaming) return

    const effectiveMode = msgMode || mode
    let acceptedMessageId: string | undefined

    try {
      // 1. 사용자 메시지를 optimistic update로 store에 추가
      const tempUserMessage = {
        role: 'user' as const,
        content,
        timestamp: Date.now() / 1000,
        status: 'completed' as const,
        metadata: { mode: effectiveMode },
      }
      // switchThread는 같은 스레드에 메시지가 있으면 스킵하므로 setState로 직접 추가
      useChatStore.setState((state) => ({
        messages: [...state.messages, tempUserMessage],
      }))

      // 2. POST 응답에서 바로 SSE를 소비한다. GET stream은 재접속 경로로 유지한다.
      const store = useChatStore.getState()
      const abortController = store._startStreaming()
      await sendMessageStream(
        threadId,
        {
          query: content,
          mode: effectiveMode,
          model,
          context: contentContextEnabled && threadContentId
            ? { content_id: threadContentId }
            : undefined,
        },
        {
          onAccepted: ({ message_id }) => {
            acceptedMessageId = message_id
            connectedMessageIdRef.current = message_id
            setSearchParams((prev) => {
              prev.set('messageId', message_id)
              return prev
            })
          },
          onToken: store.appendStreamingContent,
          onContent: store.setStreamingContent,
          onThinkingStep: store.addThinkingStep,
          onSource: store._addSource,
          onSources: store.setSources,
          onSearchQueries: store._setSearchQueries,
          onComplete: (message) => {
            store.finishStreaming(message.content, message.metadata, acceptedMessageId)
            setMessageStatus('completed')
            setSearchParams((prev) => {
              prev.delete('messageId')
              return prev
            })
          },
          onError: () => {},
        },
        abortController.signal
      )
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return
      console.error('[ChatPage v1.0.0] Failed to send message:', err)
      toast.error('메시지 전송에 실패했습니다')
      const current = useChatStore.getState()
      current.cancelStreaming()
      // 서버가 접수하기 전 실패한 경우에만 optimistic update를 되돌린다.
      if (!acceptedMessageId) useChatStore.setState({ messages })
    }
  }

  const handleRegenerate = async (regenMode?: string, model?: string) => {
    await regenerate(regenMode || mode, model)
  }

  // ============================================================
  // 렌더링
  // ============================================================
  return (
    <div className="h-full flex flex-col">
      {threadContentId && (
        <ContentBanner
          contentId={threadContentId}
          contextEnabled={contentContextEnabled}
          onContextToggle={setContentContextEnabled}
        />
      )}
      <div className="flex-1 min-h-0">
        <ChatArea
          messages={messages as any}
          isStreaming={streaming.isStreaming}
          currentStreamingMessage={streaming.currentMessage}
          currentThinkingSteps={streaming.thinkingSteps as any}
          currentSources={streaming.sources as any}
          onSendMessage={handleSendMessage}
          onRegenerate={handleRegenerate}
        />
      </div>
    </div>
  )
}
