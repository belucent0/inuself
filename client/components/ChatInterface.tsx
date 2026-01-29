"use client"

import * as React from "react"
import { ChatPrompt } from "@/components/ChatPrompt"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import { formatErrorForUser } from "@/lib/errors"
import MarkdownContent from "@/components/MarkdownContent"
import {
    Loader2,
    Sparkles,
    BookOpen,
    Globe,
    Database,
    MessageSquare,
    Brain
} from "lucide-react"

import { SourceCarousel } from "@/components/SourceCarousel"
import { ThinkingProcessAccordion } from "@/components/ThinkingProcessAccordion"
import { AIModeSelector, AIModeBadge, AI_MODE_CONFIG, type AIMode } from "@/components/AIModeSelector"

interface SearchSource {
    position: number
    title: string
    url: string
    snippet: string
    engine?: string
    source?: 'web' | 'rag'  // 소스 타입 구분
}

interface ThinkingStep {
    step: string
    content: string
    timestamp?: number
}

interface QueryAnalysis {
    original_query: string
    reformulated_query: string
    search_queries: string[]
    keywords: string[]
    search_focus: string
}

interface Message {
    id: string
    role: 'user' | 'assistant'
    content: string
    mode?: AIMode
    sources?: SearchSource[]
    thinkingSteps?: ThinkingStep[]  // 사고 과정 단계
    queryAnalysis?: QueryAnalysis   // 쿼리 분석 결과 (Perplexity 스타일)
    isStreaming?: boolean
    status?: string
}

// 진행 상태 표시
function ThinkingProcess({ status, mode }: { status: string; mode?: AIMode }) {
    const config = mode ? AI_MODE_CONFIG[mode] : AI_MODE_CONFIG.simple

    return (
        <div className={cn(
            "flex items-center gap-3 p-3 mb-4 rounded-lg border border-border/50 animate-in fade-in slide-in-from-left-2 duration-300",
            config.bgColor
        )}>
            <div className="relative shrink-0">
                <div className={cn("absolute inset-0 rounded-full animate-ping opacity-30", config.bgColor)} />
                <div className={cn("relative rounded-full p-1.5 border shadow-sm bg-background", config.color)}>
                    <Loader2 className="h-4 w-4 animate-spin" />
                </div>
            </div>
            <span className="text-sm font-medium text-foreground">
                {status}
            </span>
        </div>
    )
}

// 쿼리 분석 결과 표시 (Multi-Query 스타일)
function QueryAnalysisDisplay({ analysis }: { analysis: QueryAnalysis }) {
    const hasQueries = analysis.search_queries && analysis.search_queries.length > 0

    if (!hasQueries) return null

    return (
        <div className="mb-4 p-3 rounded-lg bg-gradient-to-r from-blue-500/10 to-cyan-500/10 border border-blue-500/20 animate-in fade-in slide-in-from-left-2 duration-300">
            <div className="flex items-center gap-2 mb-2">
                <Globe className="h-4 w-4 text-blue-500" />
                <span className="text-sm font-medium text-blue-600 dark:text-blue-400">
                    검색 중...
                </span>
            </div>
            <div className="flex flex-wrap gap-2 mt-2">
                {analysis.search_queries.map((q, i) => (
                    <span
                        key={i}
                        className="inline-flex items-center px-2.5 py-1 text-xs rounded-md bg-blue-500/15 text-blue-700 dark:text-blue-300 border border-blue-500/30"
                    >
                        <span className="w-4 h-4 flex items-center justify-center rounded-full bg-blue-500/20 text-[10px] font-bold mr-1.5">
                            {i + 1}
                        </span>
                        {q}
                    </span>
                ))}
            </div>
        </div>
    )
}

// 모드 아이콘
function getModeIcon(mode?: AIMode) {
    switch (mode) {
        case 'search': return <Globe className="h-4 w-4" />
        case 'rag': return <Database className="h-4 w-4" />
        case 'reasoning': return <Brain className="h-4 w-4" />
        case 'hybrid': return <Sparkles className="h-4 w-4" />
        default: return <MessageSquare className="h-4 w-4" />
    }
}

export function ChatInterface() {
    const [messages, setMessages] = React.useState<Message[]>([])
    const [input, setInput] = React.useState('')
    const [isLoading, setIsLoading] = React.useState(false)
    const [mode, setMode] = React.useState<AIMode>('search')
    const [conversationId, setConversationId] = React.useState<string | null>(null)

    const scrollRef = React.useRef<HTMLDivElement>(null)
    const scrollAreaRef = React.useRef<HTMLDivElement>(null)
    const isUserScrollingRef = React.useRef(false)
    const lastScrollTopRef = React.useRef(0)

    // 사용자가 맨 아래 근처에 있는지 확인 (100px 여유)
    const isNearBottom = React.useCallback(() => {
        const viewport = scrollAreaRef.current?.querySelector('[data-radix-scroll-area-viewport]') as HTMLElement
        if (!viewport) return true
        const { scrollTop, scrollHeight, clientHeight } = viewport
        return scrollHeight - scrollTop - clientHeight < 100
    }, [])

    // 스크롤 이벤트 핸들러 - 사용자가 위로 스크롤했는지 감지
    const handleScroll = React.useCallback(() => {
        const viewport = scrollAreaRef.current?.querySelector('[data-radix-scroll-area-viewport]') as HTMLElement
        if (!viewport) return

        const currentScrollTop = viewport.scrollTop
        // 사용자가 위로 스크롤하면 자동 스크롤 비활성화
        if (currentScrollTop < lastScrollTopRef.current - 10) {
            isUserScrollingRef.current = true
        }
        // 사용자가 맨 아래 근처로 스크롤하면 자동 스크롤 다시 활성화
        if (isNearBottom()) {
            isUserScrollingRef.current = false
        }
        lastScrollTopRef.current = currentScrollTop
    }, [isNearBottom])

    // 스크롤 이벤트 리스너 등록
    React.useEffect(() => {
        const viewport = scrollAreaRef.current?.querySelector('[data-radix-scroll-area-viewport]') as HTMLElement
        if (viewport) {
            viewport.addEventListener('scroll', handleScroll, { passive: true })
            return () => viewport.removeEventListener('scroll', handleScroll)
        }
    }, [handleScroll])

    // 새 메시지가 추가되면 자동 스크롤 활성화 (사용자 메시지 전송 시)
    const prevMessageCountRef = React.useRef(0)
    React.useEffect(() => {
        if (messages.length > prevMessageCountRef.current) {
            // 새 메시지가 추가되면 자동 스크롤 다시 활성화
            isUserScrollingRef.current = false
        }
        prevMessageCountRef.current = messages.length
    }, [messages.length])

    // 스크롤 자동 이동 (사용자가 위로 스크롤하지 않았을 때만)
    React.useEffect(() => {
        if (scrollRef.current && !isUserScrollingRef.current) {
            scrollRef.current.scrollIntoView({ behavior: 'smooth' })
        }
    }, [messages.length, messages[messages.length - 1]?.content, messages[messages.length - 1]?.status])

    const getApiUrl = (endpoint: string) => {
        const isLocalhost = typeof window !== 'undefined' &&
            (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
        return isLocalhost
            ? `http://localhost:8000/api/${endpoint}`
            : `${window.location.origin}/api/${endpoint}`
    }

    const handleSendMessage = async (text: string) => {
        if (!text.trim() || isLoading) return

        // 사용자 메시지 추가
        const userMessage: Message = {
            id: Date.now().toString(),
            role: 'user',
            content: text,
            mode: mode
        }
        setMessages(prev => [...prev, userMessage])
        setInput('')
        setIsLoading(true)

        // 어시스턴트 메시지 초기화
        const assistantMessageId = (Date.now() + 1).toString()
        const assistantMessage: Message = {
            id: assistantMessageId,
            role: 'assistant',
            content: '',
            mode: mode,
            isStreaming: true,
            status: '질문 분석 중...',
            thinkingSteps: []
        }
        setMessages(prev => [...prev, assistantMessage])

        try {
            // 새 AI Agent API 호출
            const response = await fetch(getApiUrl('ai/chat/stream'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query: text,
                    mode: mode,
                    conversation_id: conversationId
                })
            })

            if (!response.ok) {
                throw new Error('AI 요청 실패')
            }

            const reader = response.body?.getReader()
            const decoder = new TextDecoder()

            if (!reader) throw new Error('No response body')

            let buffer = ''
            let accumulatedContent = ''
            let accumulatedSources: SearchSource[] = []
            let accumulatedThinking: ThinkingStep[] = []

            while (true) {
                const { done, value } = await reader.read()
                if (done) break

                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n\n')
                buffer = lines.pop() || ''

                for (const line of lines) {
                    // SSE format: "data: {...}"
                    if (!line.startsWith('data: ')) continue

                    const dataStr = line.replace('data: ', '').trim()
                    if (!dataStr) continue

                    let parsed: { type: string; data: any }
                    try {
                        parsed = JSON.parse(dataStr)
                    } catch {
                        continue
                    }

                    const event = parsed.type
                    const eventData = parsed.data

                    if (event === 'done') {
                        setMessages(prev => {
                            const updated = [...prev]
                            const lastMsg = updated.find(m => m.id === assistantMessageId)
                            if (lastMsg) {
                                lastMsg.isStreaming = false
                                lastMsg.status = undefined
                            }
                            return updated
                        })
                        continue
                    }

                    if (event === 'error') {
                        // 서버 에러를 사용자 친화적 메시지로 변환
                        const userMessage = formatErrorForUser(eventData)
                        setMessages(prev => {
                            const updated = [...prev]
                            const lastMsg = updated.find(m => m.id === assistantMessageId)
                            if (lastMsg) {
                                lastMsg.content = userMessage
                                lastMsg.isStreaming = false
                                lastMsg.status = undefined
                            }
                            return updated
                        })
                        continue
                    }

                    if (event === 'thinking') {
                        // 사고 과정 업데이트
                        const thinkingData = eventData || {}
                        accumulatedThinking.push({
                            step: thinkingData.step,
                            content: thinkingData.content,
                            timestamp: Date.now()
                        })

                        setMessages(prev => {
                            const updated = [...prev]
                            const lastMsg = updated.find(m => m.id === assistantMessageId)
                            if (lastMsg) {
                                lastMsg.status = thinkingData.content
                                lastMsg.thinkingSteps = [...accumulatedThinking]

                                // 모드 업데이트 (Intent Parser 결과)
                                if (thinkingData.mode) {
                                    lastMsg.mode = thinkingData.mode.replace('AIMode.', '').toLowerCase() as AIMode
                                }
                            }
                            return updated
                        })
                    } else if (event === 'token') {
                        // 토큰 단위 스트리밍 - 점진적으로 콘텐츠 축적
                        const token = typeof eventData === 'string' ? eventData : String(eventData || '')
                        accumulatedContent += token

                        setMessages(prev => {
                            const updated = [...prev]
                            const lastMsg = updated.find(m => m.id === assistantMessageId)
                            if (lastMsg) {
                                lastMsg.content = accumulatedContent
                                lastMsg.status = undefined
                            }
                            return updated
                        })
                    } else if (event === 'sources') {
                        // 소스 업데이트
                        const sourcesData = Array.isArray(eventData) ? eventData : []
                        accumulatedSources = sourcesData.map((s: any, i: number) => ({
                            position: i + 1,
                            title: s.title || '',
                            url: s.url || '',
                            snippet: s.snippet || '',
                            engine: s.engine,
                            source: s.source || 'web'
                        }))

                        setMessages(prev => {
                            const updated = [...prev]
                            const lastMsg = updated.find(m => m.id === assistantMessageId)
                            if (lastMsg) {
                                lastMsg.sources = accumulatedSources
                                lastMsg.status = undefined
                            }
                            return updated
                        })
                    } else if (event === 'content') {
                        // 콘텐츠 업데이트
                        accumulatedContent = typeof eventData === 'string' ? eventData : String(eventData || '')

                        setMessages(prev => {
                            const updated = [...prev]
                            const lastMsg = updated.find(m => m.id === assistantMessageId)
                            if (lastMsg) {
                                lastMsg.content = accumulatedContent
                                lastMsg.status = undefined
                            }
                            return updated
                        })
                    } else if (event === 'conversation_id') {
                        // 대화 ID 저장
                        setConversationId(eventData)
                    } else if (event === 'query_analysis') {
                        // 쿼리 분석 결과 (Perplexity 스타일)
                        setMessages(prev => {
                            const updated = [...prev]
                            const lastMsg = updated.find(m => m.id === assistantMessageId)
                            if (lastMsg) {
                                lastMsg.queryAnalysis = eventData as QueryAnalysis
                            }
                            return updated
                        })
                    }
                }
            }

        } catch (error) {
            console.error('AI chat error:', error)
            setMessages(prev => {
                const updated = [...prev]
                const lastMsg = updated.find(m => m.id === assistantMessageId)
                if (lastMsg) {
                    lastMsg.content = '죄송합니다. 요청 처리 중 오류가 발생했습니다.'
                    lastMsg.isStreaming = false
                    lastMsg.status = undefined
                }
                return updated
            })
        } finally {
            setIsLoading(false)
        }
    }

    const handleModeChange = (newMode: AIMode) => {
        setMode(newMode)
    }

    const handleNewChat = () => {
        setMessages([])
        setConversationId(null)
    }

    return (
        <div className="flex flex-col h-[calc(85vh-4rem)] md:h-[calc(100vh-4rem)] relative">
            {/* 헤더: 모드 선택 */}
            <div className="flex items-center justify-between px-4 py-2 border-b bg-background/80 backdrop-blur-sm">
                <AIModeSelector mode={mode} onModeChange={handleModeChange} disabled={isLoading} />
                {conversationId && (
                    <button
                        onClick={handleNewChat}
                        className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                    >
                        새 대화
                    </button>
                )}
            </div>

            <ScrollArea className="flex-1 px-4" ref={scrollAreaRef}>
                {messages.length === 0 ? (
                    <div className="flex flex-col items-center justify-center p-8 mt-20 opacity-50 space-y-4 animate-in fade-in zoom-in-95 duration-500">
                        <div className={cn(
                            "p-4 rounded-full mb-4",
                            AI_MODE_CONFIG[mode].bgColor
                        )}>
                            <div className={cn("h-12 w-12", AI_MODE_CONFIG[mode].color)}>
                                {getModeIcon(mode)}
                            </div>
                        </div>
                        <h2 className="text-3xl font-bold bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 bg-clip-text text-transparent">
                            {AI_MODE_CONFIG[mode].label} 모드
                        </h2>
                        <p className="text-muted-foreground text-sm">
                            {AI_MODE_CONFIG[mode].description}
                        </p>
                    </div>
                ) : (
                    <div className="flex flex-col gap-6 max-w-3xl mx-auto py-8 pb-32">
                        {messages.map((msg) => (
                            <div
                                key={msg.id}
                                className={cn(
                                    "flex gap-4 w-full",
                                    msg.role === 'user' ? "justify-end" : "justify-start"
                                )}
                            >
                                <div
                                    className={cn(
                                        "w-full max-w-[90%]",
                                        msg.role === 'user'
                                            ? "px-5 py-3 bg-secondary text-secondary-foreground rounded-2xl rounded-tr-sm whitespace-pre-wrap ml-auto w-fit"
                                            : "space-y-4"
                                    )}
                                >
                                    {msg.role === 'user' ? (
                                        <div className="flex items-center gap-2">
                                            <div className={cn("shrink-0", AI_MODE_CONFIG[msg.mode || 'simple'].color)}>
                                                {getModeIcon(msg.mode)}
                                            </div>
                                            <span className="text-base">{msg.content}</span>
                                        </div>
                                    ) : (
                                        <>
                                            {/* 1. 진행 상태 */}
                                            {msg.status && <ThinkingProcess status={msg.status} mode={msg.mode} />}

                                            {/* 1.5. 쿼리 분석 결과 (Perplexity 스타일) */}
                                            {msg.queryAnalysis && (
                                                <QueryAnalysisDisplay analysis={msg.queryAnalysis} />
                                            )}

                                            {/* 2. 사고 과정 (Reasoning 모드) */}
                                            {msg.thinkingSteps && msg.thinkingSteps.length > 0 && (
                                                <ThinkingProcessAccordion
                                                    steps={msg.thinkingSteps}
                                                    isStreaming={msg.isStreaming}
                                                />
                                            )}

                                            {/* 3. 검색 소스 */}
                                            {msg.sources && msg.sources.length > 0 && (
                                                <div className="space-y-3 animate-in fade-in slide-in-from-bottom-2 duration-500">
                                                    <div className="flex items-center gap-2 text-sm font-semibold text-muted-foreground px-1">
                                                        <BookOpen className="h-4 w-4" />
                                                        <span>참조된 자료 ({msg.sources.length})</span>
                                                    </div>
                                                    <SourceCarousel sources={msg.sources} />
                                                </div>
                                            )}

                                            {/* 4. 답변 내용 */}
                                            {msg.content && (
                                                <div className="animate-in fade-in duration-300">
                                                    <MarkdownContent
                                                        content={msg.content}
                                                        sources={msg.sources}
                                                        isStreaming={msg.isStreaming}
                                                    />
                                                </div>
                                            )}
                                        </>
                                    )}
                                </div>
                            </div>
                        ))}
                        <div ref={scrollRef} />
                    </div>
                )}
            </ScrollArea>

            <div className="absolute bottom-0 left-0 right-0 p-4 bg-background/80 backdrop-blur-sm z-10 transition-all">
                <ChatPrompt
                    input={input}
                    onInputChange={setInput}
                    onSendMessage={handleSendMessage}
                    isLoading={isLoading}
                    mode={mode}
                    onModeChange={handleModeChange}
                    messages={messages}
                />
            </div>
        </div>
    )
}
