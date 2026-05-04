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
import { httpClient } from '@/shared/services'
import { ChatArea } from '@/features/chat/components/ChatArea'
import { ContentBanner } from '@/features/chat/components/ContentBanner'
import { toast } from 'sonner'
import { getAccessToken } from '@/shared/services/authToken'

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
  const eventSourceRef = useRef<EventSource | null>(null)
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

    // SSE 연결
    const accessToken = getAccessToken()
    const streamUrl = `${httpClient.getBaseUrl()}/threads/${threadId}/messages/${messageId}/stream${
      accessToken ? `?access_token=${encodeURIComponent(accessToken)}` : ''
    }`
    const eventSource = new EventSource(streamUrl)
    eventSourceRef.current = eventSource

    // 스트리밍 모드 시작 (UI 상태 업데이트)
    // 매번 getState()를 호출하여 최신 액션을 사용해야 React 리렌더링이 트리거됨
    useChatStore.getState().startStreamingMode()

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        const eventType = data.type
        const eventData = data.data

        // 중요: 매 이벤트마다 getState()를 호출하여 최신 액션 참조
        // 한 번만 호출하고 재사용하면 React 컴포넌트 리렌더링이 안됨
        const store = useChatStore.getState()

        switch (eventType) {
          case 'status':
            // 상태 변화 이벤트
            setMessageStatus(eventData as MessageStatus)
            break

          case 'partial_restore':
            // 재연결 시 기존 부분 컨텐츠 복구
            store.setStreamingContent(eventData || '')
            break

          case 'thinking_step':
            // 사고 과정 추가
            store.addThinkingStep(eventData)
            break

          case 'query_analysis':
            // 쿼리 분석 결과
            store.addThinkingStep({ type: 'query_analysis', ...eventData })
            break

          case 'sources':
            // 참고 자료
            store.setSources(eventData || [])
            break

          case 'citations':
            // 출처 표시
            // store에 citations 추가 필요 시 여기서 처리
            break

          case 'search_queries':
          case 'search_results':
            // 검색 관련 이벤트 (UI에서 필요시 처리)
            break

          case 'token':
            // 토큰 스트리밍
            store.appendStreamingContent(eventData || '')
            break

          case 'content':
            // 전체 콘텐츠 업데이트
            store.setStreamingContent(eventData || '')
            break

          case 'partial_save':
            // 2초마다 DB 저장 알림 (클라이언트에서는 무시)
            break

          case 'done':
            // 스트리밍 완료
            console.log('[ChatPage v1.0.0] Streaming done')
            store.finishStreaming(eventData?.content || '', eventData?.metadata || {}, messageId)
            setMessageStatus('completed')
            eventSource.close()
            // messageId 파라미터 제거
            setSearchParams((prev) => {
              prev.delete('messageId')
              return prev
            })
            break

          case 'error':
            // 에러 발생
            console.error('[ChatPage v1.0.0] Stream error:', eventData)
            toast.error(`오류: ${eventData}`)
            setMessageStatus('failed')
            eventSource.close()
            break
        }
      } catch (err) {
        console.error('[ChatPage v1.0.0] Failed to parse event:', err)
      }
    }

    eventSource.onerror = (err) => {
      console.error('[ChatPage v1.0.0] SSE connection error:', err)
      // 재연결 시도하지 않고 에러 처리 (브라우저가 자동 재연결)
    }

    // 클린업
    return () => {
      console.log('[ChatPage v1.0.0] Closing SSE connection')
      eventSource.close()
      eventSourceRef.current = null
      // connectedMessageIdRef는 리셋하지 않음 - 같은 messageId로 재연결 방지
    }
  }, [messageId, threadId, setSearchParams])

  // 페이지 떠날 때 SSE 연결 정리
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }
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

      // 2. POST /api/threads/{threadId}/messages (auth 헤더 포함)
      const accessToken = getAccessToken()
      const response = await fetch(`${httpClient.getBaseUrl()}/threads/${threadId}/messages`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify({
          query: content,
          mode: effectiveMode,
          model,
          context: contentContextEnabled && threadContentId
            ? { content_id: threadContentId }
            : undefined,
        }),
      })

      if (!response.ok) {
        throw new Error('메시지 전송 실패')
      }

      const { message_id } = await response.json()

      // 3. connectedMessageIdRef 리셋하여 SSE 재연결 허용
      connectedMessageIdRef.current = null

      // 4. messageId 파라미터 설정하여 SSE 연결 트리거
      setSearchParams((prev) => {
        prev.set('messageId', message_id)
        return prev
      })
    } catch (err) {
      console.error('[ChatPage v1.0.0] Failed to send message:', err)
      toast.error('메시지 전송에 실패했습니다')
      // 에러 시 optimistic update 롤백 (추가한 사용자 메시지 제거)
      useChatStore.setState({ messages })
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
