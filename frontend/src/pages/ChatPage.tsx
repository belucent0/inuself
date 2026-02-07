/**
 * ChatPage - /chat/:threadId 라우트
 *
 * 심플한 ChatArea 기반 채팅 페이지
 */

import { useParams, useSearchParams, useNavigate } from 'react-router-dom'
import { useEffect, useCallback } from 'react'
import { useThread } from '@/shared/hooks/useThreads'
import { useThreadChat } from '@/shared/hooks/useThreadChat'
import { useThreadTitle } from '@/shared/contexts/ThreadTitleContext'
import { threadsApi } from '@/shared/services'
import { ChatArea } from '@/features/chat/components/ChatArea'
import { toast } from 'sonner'

export function ChatPage() {
  const { threadId } = useParams<{ threadId: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { thread, isLoading: isThreadLoading } = useThread(threadId || '')
  const { setThreadTitle, registerHandlers, unregisterHandlers } = useThreadTitle()

  // 스레드 제목을 헤더에 표시
  useEffect(() => {
    if (thread?.title) {
      setThreadTitle(thread.title)
    } else {
      setThreadTitle('AI 채팅')
    }
  }, [thread?.title, setThreadTitle])

  // 편집 핸들러
  const handleEdit = useCallback(async () => {
    const newTitle = window.prompt('새 제목을 입력하세요', thread?.title || '')
    if (newTitle && newTitle.trim() && threadId) {
      try {
        await threadsApi.updateThreadTitle(threadId, newTitle.trim())
        setThreadTitle(newTitle.trim())
        toast.success('제목이 변경되었습니다')
      } catch (err) {
        toast.error('제목 변경에 실패했습니다')
      }
    }
  }, [thread?.title, threadId, setThreadTitle])

  // 삭제 핸들러
  const handleDelete = useCallback(async () => {
    if (window.confirm('이 대화를 삭제하시겠습니까?') && threadId) {
      try {
        await threadsApi.deleteThread(threadId)
        toast.success('대화가 삭제되었습니다')
        navigate('/')
      } catch (err) {
        toast.error('대화 삭제에 실패했습니다')
      }
    }
  }, [threadId, navigate])

  // 핸들러 등록
  useEffect(() => {
    registerHandlers(handleEdit, handleDelete)
    return () => unregisterHandlers()
  }, [handleEdit, handleDelete, registerHandlers, unregisterHandlers])

  // 초기 메시지를 Message 형식으로 변환
  const initialMessages = thread?.messages?.map((m) => ({
    role: m.role as 'user' | 'assistant',
    content: m.content,
    timestamp: m.timestamp,
    metadata: m.metadata ? {
      sources: m.metadata.sources,
      thinking_steps: m.metadata.thinking_steps,
      mode: m.metadata.mode as any,
    } : undefined,
  })) || []

  const {
    messages,
    isStreaming,
    currentStreamingMessage,
    currentThinkingSteps,
    currentSources,
    sendMessage,
    regenerate,
    requestAIResponse,
  } = useThreadChat({
    threadId: threadId || '',
    initialMessages,
  })

  // autoRequest 파라미터가 있으면 자동으로 AI 응답 요청
  useEffect(() => {
    const autoRequest = searchParams.get('autoRequest')
    const mode = searchParams.get('mode') || 'auto'

    if (autoRequest === 'true' && messages.length > 0 && !isStreaming) {
      const lastUserMessage = messages.filter(m => m.role === 'user').pop()
      if (lastUserMessage && messages[messages.length - 1].role === 'user') {
        requestAIResponse(lastUserMessage.content, mode)
      }
    }
  }, [searchParams, messages.length])

  if (isThreadLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  const handleSendMessage = async (content: string) => {
    await sendMessage(content)
  }

  // ChatPage 전용 레이아웃: main의 패딩을 상쇄하고 전체 높이 사용
  return (
    <div className="h-full">
      <ChatArea
        messages={messages as any}
        isStreaming={isStreaming}
        currentStreamingMessage={currentStreamingMessage}
        currentThinkingSteps={currentThinkingSteps as any}
        currentSources={currentSources as any}
        onSendMessage={handleSendMessage}
        onRegenerate={regenerate}
      />
    </div>
  )
}
