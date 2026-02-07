/**
 * HomePage - 랜딩 페이지 (/)
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Sparkles, ArrowRight } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { ChatInput, type AIMode, AI_MODE_CONFIG } from '@/features/chat'
import { cn } from '@/shared/utils/cn'

const suggestedQueries = [
  { text: '최근 AI 기술 트렌드는?', mode: 'search' as AIMode },
  { text: '내 문서에서 중요한 내용 요약해줘', mode: 'rag' as AIMode },
  { text: '이 주제를 단계별로 분석해줘', mode: 'reasoning' as AIMode },
]

export function HomePage() {
  const navigate = useNavigate()
  const [input, setInput] = useState('')
  const [mode, setMode] = useState<AIMode>('search')
  const [isLoading, setIsLoading] = useState(false)

  const handleSendMessage = async (text: string) => {
    if (!text.trim() || isLoading) return

    setIsLoading(true)
    try {
      // SSE 스트림으로 새 스레드 생성 (Next.js 버전과 동일)
      const response = await fetch('/api/threads/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          query: text,
          mode,
        }),
      })

      if (!response.ok) {
        throw new Error(`Failed to create thread: ${response.statusText}`)
      }

      if (!response.body) {
        throw new Error('Response body is null')
      }

      // SSE 스트림에서 thread_id 추출
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let threadId: string | null = null

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const chunk = decoder.decode(value)
        const lines = chunk.split('\n')

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'thread_id') {
                threadId = data.data
                break
              }
            } catch (e) {
              // JSON 파싱 실패 무시
            }
          }
        }

        if (threadId) break
      }

      // 스트림 취소 (사용자 메시지는 이미 저장됨)
      reader.cancel()

      if (!threadId) {
        throw new Error('Failed to get thread ID')
      }

      // 스레드 페이지로 이동 - autoRequest로 AI 응답 자동 요청
      navigate(`/chat/${threadId}?autoRequest=true&mode=${mode}`)
    } catch (error) {
      console.error('Failed to create thread:', error)
      setIsLoading(false)
    }
  }

  const handleSuggestedQuery = (query: string, queryMode: AIMode) => {
    setMode(queryMode)
    setInput(query)
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-[calc(100vh-4rem)] px-4">
      {/* 헤더 */}
      <div className="text-center mb-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-primary/10 text-primary mb-6">
          <Sparkles className="h-4 w-4" />
          <span className="text-sm font-medium">AI 어시스턴트</span>
        </div>
        <h1 className="text-4xl md:text-5xl font-bold mb-4 bg-gradient-to-r from-primary via-purple-500 to-pink-500 bg-clip-text text-transparent">
          무엇이든 물어보세요
        </h1>
        <p className="text-muted-foreground text-lg max-w-md mx-auto">
          웹 검색, 문서 검색, 추론 등 다양한 방식으로 답변을 제공합니다.
        </p>
      </div>

      {/* 입력 영역 */}
      <div className="w-full max-w-2xl animate-in fade-in slide-in-from-bottom-6 duration-700 delay-100">
        <ChatInput
          input={input}
          onInputChange={setInput}
          onSendMessage={handleSendMessage}
          isLoading={isLoading}
          mode={mode}
          onModeChange={setMode}
          showModeDescription
        />
      </div>

      {/* 추천 질문 */}
      <div className="mt-8 flex flex-wrap justify-center gap-3 max-w-2xl animate-in fade-in slide-in-from-bottom-8 duration-700 delay-200">
        {suggestedQueries.map((query, index) => (
          <Button
            key={index}
            variant="outline"
            className={cn(
              'group rounded-full px-4 py-2 h-auto transition-all hover:scale-105',
              AI_MODE_CONFIG[query.mode].bgColor,
              'border-transparent hover:border-primary/20'
            )}
            onClick={() => handleSuggestedQuery(query.text, query.mode)}
            disabled={isLoading}
          >
            <span className={cn('text-sm', AI_MODE_CONFIG[query.mode].color)}>
              {query.text}
            </span>
            <ArrowRight className="h-3 w-3 ml-2 opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 transition-all" />
          </Button>
        ))}
      </div>

      {/* 모드 설명 */}
      <div className="mt-12 grid grid-cols-2 md:grid-cols-4 gap-4 max-w-3xl animate-in fade-in slide-in-from-bottom-10 duration-700 delay-300">
        {(['search', 'rag', 'reasoning', 'hybrid'] as AIMode[]).map((m) => {
          const config = AI_MODE_CONFIG[m]
          return (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={cn(
                'p-4 rounded-xl border transition-all hover:scale-105',
                mode === m
                  ? cn(config.bgColor, 'border-primary/30 shadow-sm')
                  : 'bg-card border-border/50 hover:border-primary/20'
              )}
            >
              <div className={cn('font-medium mb-1', config.color)}>{config.label}</div>
              <div className="text-xs text-muted-foreground">{config.description}</div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
