"use client"

import * as React from "react"
import { ChatPrompt } from "@/components/ChatPrompt"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import MarkdownContent from "@/components/MarkdownContent"
import { Badge } from "@/components/ui/badge"
import { ExternalLink, Search, MessageSquare, Loader2, Sparkles, BookOpen, ChevronRight, ChevronLeft } from "lucide-react"

import { SourceCarousel } from "@/components/SourceCarousel"
import { ThinkingProcessAccordion } from "@/components/ThinkingProcessAccordion"

type ChatMode = 'chat' | 'search'

interface SearchSource {
    position: number
    title: string
    url: string
    snippet: string
    engine: string
}

interface Message {
    id: string
    role: 'user' | 'assistant'
    content: string
    mode?: ChatMode
    sources?: SearchSource[]
    citationsUsed?: number[]
    isSearching?: boolean // 검색/생성 중 여부
    status?: string // 현재 진행 상태 메시지 (예: "웹 검색 중...", "분석 중...")
}

// 생각하는 과정 표시 (애니메이션)
function ThinkingProcess({ status }: { status: string }) {
    return (
        <div className="flex items-center gap-3 p-3 mb-4 rounded-lg bg-muted/50 border border-border/50 animate-in fade-in slide-in-from-left-2 duration-300">
            <div className="relative shrink-0">
                <div className="absolute inset-0 bg-primary/20 rounded-full animate-ping" />
                <div className="relative bg-background rounded-full p-1.5 border shadow-sm">
                    <Loader2 className="h-4 w-4 animate-spin text-primary" />
                </div>
            </div>
            <span className="text-sm font-medium text-foreground">
                {status}
            </span>
        </div>
    )
}

export function ChatInterface() {
    const [messages, setMessages] = React.useState<Message[]>([])
    const [input, setInput] = React.useState('')
    const [isLoading, setIsLoading] = React.useState(false)
    const [mode, setMode] = React.useState<ChatMode>('search')
    const [isReasoning, setIsReasoning] = React.useState(false) // 추론 모드 상태

    const scrollRef = React.useRef<HTMLDivElement>(null)

    // 메시지가 추가되거나 내용이 바뀌면 스크롤
    React.useEffect(() => {
        if (scrollRef.current) {
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

    const handleSearchMessage = async (text: string) => {
        const userMessage: Message = {
            id: Date.now().toString(),
            role: 'user',
            content: text,
            mode: 'search'
        }

        setMessages(prev => [...prev, userMessage])
        setInput('')
        setIsLoading(true)

        // 초기 어시스턴트 메시지 (빈 상태)
        const assistantMessageId = (Date.now() + 1).toString()
        const assistantMessage: Message = {
            id: assistantMessageId,
            role: 'assistant',
            content: '',
            mode: 'search',
            isSearching: true,
            status: '웹 검색을 시작합니다...'
        }
        setMessages(prev => [...prev, assistantMessage])

        try {
            const response = await fetch(getApiUrl('search'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query: text,
                    max_results: 10,
                    language: 'ko-KR',
                    reasoning_mode: isReasoning // 추론 모드 여부 전달
                })
            })

            if (!response.ok) {
                throw new Error('검색 요청 실패')
            }

            const reader = response.body?.getReader()
            const decoder = new TextDecoder()

            if (!reader) throw new Error('No response body')

            let buffer = ''
            let accumulatedContent = '' // 전체 답변 내용을 누적할 변수

            while (true) {
                const { done, value } = await reader.read()
                if (done) break

                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n\n')
                buffer = lines.pop() || '' // 불완전한 마지막 라인 보관

                for (const line of lines) {
                    if (!line.startsWith('event: ')) continue

                    const [eventLine, dataLine] = line.split('\n')
                    const event = eventLine.replace('event: ', '').trim()
                    const dataStr = dataLine.replace('data: ', '').trim()

                    if (event === 'done') {
                        // 완료 처리
                        setMessages(prev => {
                            const updated = [...prev]
                            const lastMsg = updated.find(m => m.id === assistantMessageId)
                            if (lastMsg) {
                                lastMsg.isSearching = false
                                lastMsg.status = undefined // 상태 메시지 제거
                            }
                            return updated
                        })
                        continue
                    }

                    if (event === 'error') {
                        // 에러 처리
                        try {
                            const errorMsg = JSON.parse(dataStr)
                            setMessages(prev => {
                                const updated = [...prev]
                                const lastMsg = updated.find(m => m.id === assistantMessageId)
                                if (lastMsg) {
                                    lastMsg.content = `⚠️ 오류가 발생했습니다: ${errorMsg}`
                                    lastMsg.isSearching = false
                                    lastMsg.status = undefined
                                }
                                return updated
                            })
                        } catch (e) { }
                        continue
                    }

                    try {
                        const data = JSON.parse(dataStr)

                        if (event === 'status') {
                            setMessages(prev => {
                                const updated = [...prev]
                                const lastMsg = updated.find(m => m.id === assistantMessageId)
                                if (lastMsg) lastMsg.status = data
                                return updated
                            })
                        } else if (event === 'sources') {
                            setMessages(prev => {
                                const updated = [...prev]
                                const lastMsg = updated.find(m => m.id === assistantMessageId)
                                if (lastMsg) lastMsg.sources = data
                                return updated
                            })
                        } else if (event === 'token') {
                            accumulatedContent += data // 로컬 변수에 누적

                            setMessages(prev => {
                                const updated = [...prev]
                                const lastMsg = updated.find(m => m.id === assistantMessageId)
                                if (lastMsg) {
                                    lastMsg.content = accumulatedContent // 누적된 전체 내용으로 교체 (중복 방지)
                                    
                                    // 토큰이 들어오면 상태 메시지는 더 이상 보여주지 않거나 "작성 중"으로 변경 가능
                                    // 여기서는 내용이 생기기 시작하면 상태 메시지 제거
                                    if (lastMsg.status && accumulatedContent.length > 10) {
                                        lastMsg.status = undefined
                                    }
                                }
                                return updated
                            })
                        }
                    } catch (e) {
                        console.error('JSON parse error', e)
                    }
                }
            }

        } catch (error) {
            setMessages(prev => {
                const updated = [...prev]
                const lastMsg = updated.find(m => m.id === assistantMessageId)
                if (lastMsg) {
                    lastMsg.content = '죄송합니다. 검색 중 오류가 발생했습니다.'
                    lastMsg.isSearching = false
                    lastMsg.status = undefined
                }
                return updated
            })
        } finally {
            setIsLoading(false)
        }
    }

    const handleChatMessage = async (text: string) => {
        // 기존 채팅 로직 유지 (필요하다면 여기도 스트리밍 개선 가능)
        // ... (이전 코드와 동일, 생략 없이 복원하거나 필요시 검색과 통일)
        // 여기서는 검색 기능 개선에 집중하므로 간단히 구현하거나 기존 로직 유지
        // 사용자가 "AI 모드"를 주로 쓸 것이므로 검색 로직만 고도화해도 무방함
        
        // 편의상 검색 로직으로 통일하거나, 별도 채팅 로직을 유지할 수 있음
        // 사용자 요청은 "AI 모드"에 대한 것이므로 handleSearchMessage가 메인임
        
        // 기존 채팅 로직 복원 (스트리밍 유지)
        const userMessage: Message = {
            id: Date.now().toString(),
            role: 'user',
            content: text,
            mode: 'chat'
        }
        setMessages(prev => [...prev, userMessage])
        setInput('')
        setIsLoading(true)

        try {
            const response = await fetch(getApiUrl('chat'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: [...messages, userMessage].map(m => ({ role: m.role, content: m.content }))
                }),
            })

            const reader = response.body?.getReader()
            const decoder = new TextDecoder()
            if (!reader) throw new Error('No body')

            const assistantMessage: Message = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: '',
                mode: 'chat'
            }
            setMessages(prev => [...prev, assistantMessage])

            let accumulatedContent = ''
            let buffer = ''

            while (true) {
                const { done, value } = await reader.read()
                if (done) break
                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n')
                buffer = lines.pop() || ''
                
                for (const line of lines) {
                    if (line.trim() === '') continue
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6).trim()
                        if (data === '[DONE]') continue
                        try {
                            const parsed = JSON.parse(data)
                            const content = parsed.choices?.[0]?.delta?.content
                            if (content) {
                                accumulatedContent += content
                                setMessages(prev => {
                                    const updated = [...prev]
                                    const lastMsg = updated[updated.length - 1]
                                    if (lastMsg && lastMsg.role === 'assistant') {
                                        lastMsg.content = accumulatedContent
                                    }
                                    return updated
                                })
                            }
                        } catch(e) {}
                    }
                }
            }
        } catch (error) {
            console.error(error)
        } finally {
            setIsLoading(false)
        }
    }

    const handleSendMessage = async (text: string) => {
        if (!text.trim() || isLoading) return
        if (mode === 'search') {
            await handleSearchMessage(text)
        } else {
            await handleChatMessage(text)
        }
    }

    const handleModeChange = (newMode: ChatMode) => {
        setMode(newMode)
    }

    return (
        <div className="flex flex-col h-[calc(85vh-4rem)] md:h-[calc(100vh-4rem)] relative">
            <ScrollArea className="flex-1 px-4">
                {messages.length === 0 ? (
                    <div className="flex flex-col items-center justify-center p-8 mt-20 opacity-50 space-y-4 animate-in fade-in zoom-in-95 duration-500">
                        <div className="bg-primary/10 p-4 rounded-full mb-4">
                            {mode === 'search' ? (
                                <Sparkles className="h-12 w-12 text-indigo-500" />
                            ) : (
                                <MessageSquare className="h-12 w-12 text-primary" />
                            )}
                        </div>
                        <h2 className="text-3xl font-bold bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 bg-clip-text text-transparent">
                            {mode === 'search' ? '무엇을 도와드릴까요?' : '채팅 모드'}
                        </h2>
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
                                        "w-full max-w-[90%]", // 말풍선 너비 확장
                                        msg.role === 'user'
                                            ? "px-5 py-3 bg-secondary text-secondary-foreground rounded-2xl rounded-tr-sm whitespace-pre-wrap ml-auto w-fit"
                                            : "space-y-4"
                                    )}
                                >
                                    {msg.role === 'user' ? (
                                        <div className="flex items-center gap-2">
                                            {msg.mode === 'search' && (
                                                <Search className="h-4 w-4 text-muted-foreground" />
                                            )}
                                            <span className="text-base">{msg.content}</span>
                                        </div>
                                    ) : (
                                        <>
                                            {/* 1. 진행 상태 메시지 (검색 중, 분석 중...) */}
                                            {msg.status && <ThinkingProcess status={msg.status} />}

                                            {/* 2. 검색 소스 그리드 (캐러셀 형태) */}
                                            {msg.mode === 'search' && msg.sources && msg.sources.length > 0 && (
                                                <div className="space-y-3 animate-in fade-in slide-in-from-bottom-2 duration-500">
                                                    <div className="flex items-center gap-2 text-sm font-semibold text-muted-foreground px-1">
                                                        <BookOpen className="h-4 w-4" />
                                                        <span>검색된 자료 ({msg.sources.length})</span>
                                                    </div>
                                                    
                                                    {/* 캐러셀 컴포넌트 */}
                                                    <SourceCarousel sources={msg.sources} />
                                                </div>
                                            )}

                                            {/* 3. 생각 과정 및 답변 내용 (스트리밍) */}
                                            {msg.content && (() => {
                                                // 생각 태그 파싱
                                                const thinkMatch = msg.content.match(/<think>([\s\S]*?)<\/think>/)
                                                const isThinking = msg.content.includes('<think>') && !msg.content.includes('</think>')
                                                
                                                let thinkingContent = ''
                                                let mainContent = msg.content

                                                if (thinkMatch) {
                                                    // 완료된 생각
                                                    thinkingContent = thinkMatch[1]
                                                    mainContent = msg.content.replace(/<think>[\s\S]*?<\/think>/, '').trim()
                                                } else if (isThinking) {
                                                    // 생각 중...
                                                    thinkingContent = msg.content.replace('<think>', '')
                                                    mainContent = '' // 아직 본문 없음
                                                }

                                                return (
                                                    <div className="animate-in fade-in duration-300">
                                                        {/* 생각 과정 아코디언 */}
                                                        {thinkingContent && (
                                                            <ThinkingProcessAccordion 
                                                                content={thinkingContent} 
                                                                isStreaming={isThinking && (msg.isSearching ?? false)} 
                                                            />
                                                        )}
                                                        
                                                        {/* 메인 답변 */}
                                                        {mainContent && (
                                                            <MarkdownContent 
                                                                content={mainContent} 
                                                                sources={msg.sources} // 출처 목록 전달 (링크 생성용)
                                                            />
                                                        )}
                                                    </div>
                                                )
                                            })()}
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
                    isReasoning={isReasoning}
                    onReasoningChange={setIsReasoning}
                />
            </div>
        </div>
    )
}
