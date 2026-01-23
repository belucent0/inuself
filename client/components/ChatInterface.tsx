"use client"

import * as React from "react"
import { ChatPrompt } from "@/components/ChatPrompt"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import MarkdownContent from "@/components/MarkdownContent"
import { Badge } from "@/components/ui/badge"
import { ExternalLink, Search, MessageSquare, Loader2 } from "lucide-react"

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
    isSearching?: boolean
}

// 출처 카드 컴포넌트
function SourceCard({ source }: { source: SearchSource }) {
    return (
        <a
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-start gap-2 p-2 rounded-lg border bg-muted/30 hover:bg-muted/50 transition-colors group"
        >
            <Badge variant="outline" className="shrink-0 mt-0.5">
                {source.position}
            </Badge>
            <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1">
                    <span className="text-sm font-medium truncate group-hover:text-primary">
                        {source.title}
                    </span>
                    <ExternalLink className="h-3 w-3 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>
                <p className="text-xs text-muted-foreground line-clamp-2 mt-0.5">
                    {source.snippet}
                </p>
            </div>
        </a>
    )
}

// 검색 중 애니메이션
function SearchingIndicator() {
    return (
        <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-sm">웹에서 검색하고 있습니다...</span>
        </div>
    )
}

export function ChatInterface() {
    const [messages, setMessages] = React.useState<Message[]>([])
    const [input, setInput] = React.useState('')
    const [isLoading, setIsLoading] = React.useState(false)
    const [mode, setMode] = React.useState<ChatMode>('chat')

    const scrollRef = React.useRef<HTMLDivElement>(null)

    React.useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollIntoView({ behavior: 'smooth' })
        }
    }, [messages])

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

        // 검색 중 메시지 추가
        const searchingMessage: Message = {
            id: (Date.now() + 1).toString(),
            role: 'assistant',
            content: '',
            mode: 'search',
            isSearching: true
        }
        setMessages(prev => [...prev, searchingMessage])

        try {
            const response = await fetch(getApiUrl('search'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query: text,
                    max_results: 10,
                    language: 'ko-KR'
                })
            })

            if (!response.ok) {
                throw new Error('검색 요청 실패')
            }

            const data = await response.json()

            // 검색 중 메시지를 결과로 교체
            setMessages(prev => {
                const updated = [...prev]
                const lastIndex = updated.length - 1
                if (updated[lastIndex]?.isSearching) {
                    updated[lastIndex] = {
                        id: updated[lastIndex].id,
                        role: 'assistant',
                        content: data.summary,
                        mode: 'search',
                        sources: data.sources,
                        citationsUsed: data.citations_used
                    }
                }
                return updated
            })

        } catch (error) {
            setMessages(prev => {
                const updated = [...prev]
                const lastIndex = updated.length - 1
                if (updated[lastIndex]?.isSearching) {
                    updated[lastIndex] = {
                        id: updated[lastIndex].id,
                        role: 'assistant',
                        content: '죄송합니다. 검색 중 오류가 발생했습니다.',
                        mode: 'search'
                    }
                }
                return updated
            })
        } finally {
            setIsLoading(false)
        }
    }

    const handleChatMessage = async (text: string) => {
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
            const shortId = Math.random().toString(36).substring(2, 10)
            const traceId = `trc_${shortId}`

            const response = await fetch(getApiUrl('chat'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Trace-Id': traceId,
                },
                body: JSON.stringify({
                    messages: [...messages, userMessage].map(m => ({
                        role: m.role,
                        content: m.content
                    }))
                }),
            })

            if (!response.ok) {
                throw new Error('Failed to get response')
            }

            const reader = response.body?.getReader()
            const decoder = new TextDecoder()

            if (!reader) {
                throw new Error('No response body')
            }

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
                        } catch (e) {
                            // Parse error silently ignored
                        }
                    }
                }
            }
        } catch (error) {
            setMessages(prev => [...prev, {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: '죄송합니다. 오류가 발생했습니다.',
                mode: 'chat'
            }])
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
                    <div className="flex flex-col items-center justify-center p-8 mt-20 opacity-50 space-y-4">
                        <div className="bg-primary/10 p-4 rounded-full">
                            {mode === 'search' ? (
                                <Search className="h-8 w-8 text-primary" />
                            ) : (
                                <MessageSquare className="h-8 w-8 text-primary" />
                            )}
                        </div>
                        <h2 className="text-2xl font-semibold">
                            {mode === 'search' ? '무엇을 검색할까요?' : '무엇을 도와드릴까요?'}
                        </h2>
                        <p className="text-sm text-muted-foreground text-center max-w-md">
                            {mode === 'search'
                                ? '웹 검색 결과를 바탕으로 출처가 명시된 답변을 제공합니다.'
                                : 'AI 어시스턴트와 자유롭게 대화하세요.'}
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
                                        "max-w-[85%]",
                                        msg.role === 'user'
                                            ? "px-4 py-2.5 bg-secondary text-secondary-foreground rounded-2xl rounded-tr-sm whitespace-pre-wrap"
                                            : "space-y-3"
                                    )}
                                >
                                    {msg.role === 'user' ? (
                                        <div className="flex items-center gap-2">
                                            {msg.mode === 'search' && (
                                                <Search className="h-3 w-3 text-muted-foreground" />
                                            )}
                                            <span>{msg.content}</span>
                                        </div>
                                    ) : msg.isSearching ? (
                                        <SearchingIndicator />
                                    ) : (
                                        <>
                                            {/* 검색 모드 배지 */}
                                            {msg.mode === 'search' && msg.sources && msg.sources.length > 0 && (
                                                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                                    <Search className="h-3 w-3" />
                                                    <span>{msg.sources.length}개의 소스에서 검색</span>
                                                </div>
                                            )}

                                            {/* 답변 내용 */}
                                            <div className="px-4 py-2.5 bg-transparent text-foreground border rounded-2xl rounded-tl-sm">
                                                <MarkdownContent content={msg.content} />
                                            </div>

                                            {/* 출처 카드 (검색 모드만) */}
                                            {msg.mode === 'search' && msg.sources && msg.sources.length > 0 && (
                                                <div className="space-y-2">
                                                    <div className="text-xs font-medium text-muted-foreground px-1">
                                                        📚 출처
                                                    </div>
                                                    <div className="grid gap-2">
                                                        {msg.sources
                                                            .filter(s => msg.citationsUsed?.includes(s.position) || msg.citationsUsed?.length === 0)
                                                            .slice(0, 5)
                                                            .map((source) => (
                                                                <SourceCard key={source.position} source={source} />
                                                            ))}
                                                    </div>
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
                />
            </div>
        </div>
    )
}
