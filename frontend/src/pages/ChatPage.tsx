/**
 * ChatPage - /chat/:threadId 라우트
 *
 * Zustand store 기반 채팅 페이지 (V2)
 * - URL 변경 시 switchThread() 호출로 단순화
 * - V10: 낙관적 라우팅 지원 - threadId='new'일 때 새 스레드 생성
 */

import { useParams, useSearchParams, useNavigate } from 'react-router-dom'
import { useEffect, useCallback, useRef } from 'react'
import { useThread } from '@/shared/hooks/useThreads'
import { useChatStore } from '@/shared/stores/chatStore'
import { useThreadTitle } from '@/shared/contexts/ThreadTitleContext'
import { threadsApi } from '@/shared/services'
import { ChatArea } from '@/features/chat/components/ChatArea'
import { toast } from 'sonner'

export function ChatPage() {
  const { threadId: paramThreadId } = useParams<{ threadId: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()

  // V10: 새 스레드 생성 모드 감지
  const isNewThread = paramThreadId === 'new'
  const query = searchParams.get('query')
  const mode = searchParams.get('mode') || 'auto'

  // 실제 threadId (new일 때는 빈 문자열)
  const threadId = isNewThread ? '' : (paramThreadId || '')

  // TanStack Query로 스레드 로드 (기존 스레드일 때만)
  const { thread, isLoading: isThreadLoading } = useThread(threadId || null)

  // Zustand store
  const {
    threadId: storeThreadId,
    messages,
    streaming,
    switchThread,
    sendMessage,
    createAndStream,
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
    // 새 스레드 모드면 스킵 (createAndStream이 처리)
    if (isNewThread) return

    // 로딩 중이면 스킵
    if (isThreadLoading) return

    // 스레드 데이터가 없거나 URL과 불일치하면 스킵
    if (!thread || thread.thread_id !== threadId) return

    // 현재 스트리밍 중인 같은 스레드면 덮어쓰지 않음
    if (streaming.isStreaming && storeThreadId === threadId) return

    // 이미 같은 스레드가 설정되어 있고 메시지가 있으면 스킵
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
  }, [thread, threadId, isNewThread, isThreadLoading, storeThreadId, messages.length, streaming.isStreaming, switchThread])

  // ============================================================
  // V10: 새 스레드 생성 모드
  // ============================================================
  const hasStartedRef = useRef(false)

  useEffect(() => {
    if (hasStartedRef.current) return
    if (!isNewThread || !query) return

    hasStartedRef.current = true
    const decodedQuery = decodeURIComponent(query)

    createAndStream(decodedQuery, mode)
      .then((newThreadId) => {
        if (newThreadId) {
          navigate(`/chat/${newThreadId}`, { replace: true })
        }
      })
      .catch((err) => {
        console.error('[ChatPage] Failed to create thread:', err)
        toast.error('대화 생성에 실패했습니다')
        navigate('/')
      })
  }, [isNewThread, query, mode, navigate, createAndStream])

  // ============================================================
  // 로딩 상태
  // ============================================================
  if (!isNewThread && isThreadLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  // ============================================================
  // 이벤트 핸들러
  // ============================================================
  const handleSendMessage = async (content: string, msgMode?: string) => {
    await sendMessage(content, msgMode || mode)
  }

  const handleRegenerate = async () => {
    await regenerate(mode)
  }

  // ============================================================
  // 렌더링
  // ============================================================
  return (
    <div className="h-full">
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
  )
}
