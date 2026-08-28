/**
 * HomePage - 랜딩 페이지 (/)
 *
 * v1.0.0: 확인 후 라우팅
 * - POST /api/threads로 스레드+메시지 먼저 생성
 * - 응답 받은 후 /chat/{thread_id}?messageId={message_id} 로 이동
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChatInput, type AIMode, AI_MODE_CONFIG } from '@/features/chat'
import { cn } from '@/shared/utils/cn'
import { toast } from 'sonner'
import { httpClient } from '@/shared/services'
import type { AcceptedMessage } from '@/shared/services/chatStreamService'
import { enterAcceptedHomeThread } from './homeThreadTransition'

const suggestedQueries = [
  { text: '최근 AI 기술 트렌드는?', mode: 'search' as AIMode },
  { text: '내 문서에서 중요한 내용 요약해줘', mode: 'rag' as AIMode },
  { text: '이 주제를 단계별로 분석해줘', mode: 'reasoning' as AIMode },
]

export function HomePage() {
  const navigate = useNavigate()
  const [input, setInput] = useState('')
  const [mode, setMode] = useState<AIMode>('auto')
  const [isNavigating, setIsNavigating] = useState(false)

  const handleSendMessage = async (text: string) => {
    if (!text.trim() || isNavigating) return

    setIsNavigating(true)

    try {
      const accepted = await httpClient.post<AcceptedMessage>(
        '/threads',
        { query: text, mode }
      )

      enterAcceptedHomeThread(accepted, text, mode, navigate)
    } catch (err) {
      console.error('[HomePage] Failed to create thread:', err)
      toast.error('대화 생성에 실패했습니다')
      setIsNavigating(false)
    }
  }

  const handleSuggestedQuery = (query: string, queryMode: AIMode) => {
    setMode(queryMode)
    setInput(query)
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-4rem)] px-4">
      {/* 헤더 */}
      <div className="text-center mb-10">
        <h1 className="text-4xl md:text-5xl font-semibold mb-3 text-foreground tracking-tight">
          InuSelf
        </h1>
        <p className="text-muted-foreground text-base max-w-md mx-auto">
          웹 검색, 문서 검색, 추론 등 다양한 방식으로 답변합니다
        </p>
      </div>

      {/* 입력 영역 */}
      <div className="w-full max-w-2xl">
        <ChatInput
          input={input}
          onInputChange={setInput}
          onSendMessage={handleSendMessage}
          isLoading={isNavigating}
          mode={mode}
          onModeChange={setMode}
          showModeDescription
        />
      </div>

      {/* 추천 질문 */}
      <div className="mt-6 flex flex-wrap justify-center gap-2 max-w-2xl">
        {suggestedQueries.map((query, index) => (
          <button
            key={index}
            className="px-4 py-2 rounded-full border border-border text-sm text-muted-foreground hover:text-foreground hover:border-foreground/30 transition-colors"
            onClick={() => handleSuggestedQuery(query.text, query.mode)}
            disabled={isNavigating}
          >
            {query.text}
          </button>
        ))}
      </div>

      {/* 모드 선택 */}
      <div className="mt-10 flex gap-3 flex-wrap justify-center">
        {(['search', 'rag', 'reasoning', 'hybrid'] as AIMode[]).map((m) => {
          const config = AI_MODE_CONFIG[m]
          return (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={cn(
                'px-4 py-2 rounded-full border text-sm transition-colors',
                mode === m
                  ? 'border-foreground/40 text-foreground bg-muted'
                  : 'border-border text-muted-foreground hover:text-foreground hover:border-foreground/20'
              )}
            >
              {config.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
