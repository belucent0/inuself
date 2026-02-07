"use client"

/**
 * 스레드 대화 페이지
 *
 * AI 채팅 대화 UI
 * - AppSidebar에서 스레드 목록 관리
 * - ChatArea로 대화 인터페이스 표시
 */

import { useCallback, useState, useEffect, useRef } from 'react'
import { useParams, useSearchParams, useRouter } from 'next/navigation'
import { Button } from '@/components/ui/button'
import { ChatArea } from '@/components/ai-chat/ChatArea'
import { useThreads, useThread } from '@/lib/hooks/useThreads'
import { useThreadChat } from '@/lib/hooks/useThreadChat'
import { Message } from '@/lib/api/threads'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { useThreadTitle } from '@/lib/contexts/ThreadTitleContext'

export default function ThreadPage() {
  const params = useParams()
  const searchParams = useSearchParams()
  const router = useRouter()

  const threadId = params.threadId as string

  const [editedTitle, setEditedTitle] = useState('')
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)

  const { removeThread, changeThreadTitle } = useThreads()
  const { thread, isLoading, updateThread } = useThread(threadId)
  const { setThreadTitle, isEditingTitle, setIsEditingTitle, registerHandlers, unregisterHandlers } = useThreadTitle()

  // Vercel AI SDK 스타일 훅 사용
  const {
    messages,
    input,
    handleInputChange,
    handleSubmit,
    isStreaming,
    currentStreamingMessage,
    currentThinkingSteps,
    currentSources,
    sendMessage,
    requestAIResponse,
    regenerate,
    setMessages,
  } = useThreadChat({
    threadId,
    initialMessages: thread?.messages || [],
    onMessageComplete: (newMessage: Message) => {
      // 메시지 완료 시 스레드 업데이트
      if (thread) {
        updateThread({
          ...thread,
          messages: [...messages, newMessage],
        })
      }
    },
  })

  // 스레드 제목을 Context에 설정하고 핸들러 등록
  useEffect(() => {
    if (thread) {
      setThreadTitle(thread.title)
      registerHandlers(handleEditTitle, () => setShowDeleteDialog(true))
    }
    return () => {
      unregisterHandlers()
    }
  }, [thread, setThreadTitle, registerHandlers, unregisterHandlers])

  // 자동 요청 처리 (랜딩 페이지에서 전달된 경우)
  const autoRequestProcessed = useRef(false)
  useEffect(() => {
    const autoRequest = searchParams.get('autoRequest')
    const mode = searchParams.get('mode') || 'auto'

    // 마지막 메시지가 사용자 메시지이고, 아직 처리하지 않았으면 AI 요청 시작
    if (
      autoRequest === 'true' &&
      !autoRequestProcessed.current &&
      messages.length > 0 &&
      messages[messages.length - 1]?.role === 'user' &&
      !isStreaming
    ) {
      autoRequestProcessed.current = true
      // 마지막 사용자 메시지로 AI 응답만 요청 (사용자 메시지 추가 없이)
      const lastUserMessage = messages[messages.length - 1]
      requestAIResponse(lastUserMessage.content, mode)

      // URL에서 쿼리 파라미터 제거 (히스토리 교체)
      router.replace(`/chat/${threadId}`)
    }
  }, [searchParams, messages, isStreaming, requestAIResponse, router, threadId])

  // 메시지 전송 핸들러
  const handleSendMessage = useCallback(
    async (content: string, mode?: string) => {
      if (!thread) return
      await sendMessage(content, mode)
    },
    [thread, sendMessage]
  )

  const handleDeleteThread = async () => {
    await removeThread(threadId)
    router.push('/')
  }

  const handleSaveTitle = async () => {
    if (!editedTitle.trim() || !thread) return

    await changeThreadTitle(threadId, editedTitle)
    setThreadTitle(editedTitle) // Context 업데이트
    setIsEditingTitle(false)
  }

  if (isLoading) {
    return (
      <div className="flex h-screen">
        <div className="flex-1 p-8 space-y-4">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-[calc(100vh-200px)] w-full" />
        </div>
      </div>
    )
  }

  if (!thread) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center">
          <p className="text-lg text-muted-foreground">스레드를 찾을 수 없습니다</p>
          <Button onClick={() => router.push('/')} className="mt-4">
            새 대화 시작
          </Button>
        </div>
      </div>
    )
  }

  // 제목 편집 핸들러
  const handleEditTitle = () => {
    setEditedTitle(thread?.title || '')
    setIsEditingTitle(true)
  }

  return (
    <>
      {/* 채팅 영역 - 전체 높이 사용 */}
      <ChatArea
        messages={messages}
        isStreaming={isStreaming}
        currentStreamingMessage={currentStreamingMessage}
        currentThinkingSteps={currentThinkingSteps}
        currentSources={currentSources}
        onSendMessage={handleSendMessage}
        onRegenerate={regenerate}
      />

      {/* 제목 편집 다이얼로그 */}
      <AlertDialog open={isEditingTitle} onOpenChange={setIsEditingTitle}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>스레드 제목 변경</AlertDialogTitle>
            <AlertDialogDescription>
              새로운 제목을 입력하세요.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Input
            value={editedTitle}
            onChange={(e) => setEditedTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSaveTitle()
              if (e.key === 'Escape') setIsEditingTitle(false)
            }}
            autoFocus
            placeholder="스레드 제목"
          />
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={handleSaveTitle}>저장</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 삭제 확인 다이얼로그 */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>대화를 삭제하시겠습니까?</AlertDialogTitle>
            <AlertDialogDescription>
              이 작업은 되돌릴 수 없습니다. 대화의 모든 메시지가 삭제됩니다.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={handleDeleteThread}>삭제</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
