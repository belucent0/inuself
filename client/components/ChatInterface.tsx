"use client"

import * as React from "react"
import { ChatPrompt } from "@/components/ChatPrompt"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import MarkdownContent from "@/components/MarkdownContent"

interface Message {
    id: string
    role: 'user' | 'assistant'
    content: string
}

export function ChatInterface() {
    const [messages, setMessages] = React.useState<Message[]>([])
    const [input, setInput] = React.useState('')
    const [isLoading, setIsLoading] = React.useState(false)

    const scrollRef = React.useRef<HTMLDivElement>(null)

    React.useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollIntoView({ behavior: 'smooth' })
        }
    }, [messages])

    const handleSendMessage = async (text: string) => {
        if (!text.trim() || isLoading) return

        const userMessage: Message = {
            id: Date.now().toString(),
            role: 'user',
            content: text
        }

        setMessages(prev => [...prev, userMessage])
        setInput('')
        setIsLoading(true)

        try {
            // Next.js 개발 서버의 rewrites는 스트리밍을 버퍼링하므로
            // localhost에서는 백엔드로 직접 요청하여 스트리밍 보장
            const apiUrl = typeof window !== 'undefined' && 
                          (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
                          ? 'http://localhost:8000/api/chat'
                          : '/api/chat'
            
            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
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
                content: ''
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
                content: '죄송합니다. 오류가 발생했습니다.'
            }])
        } finally {
            setIsLoading(false)
        }
    }

    return (
        <div className="flex flex-col h-[calc(85vh-4rem)] md:h-[calc(100vh-4rem)] relative">
            <ScrollArea className="flex-1 px-4">
                {messages.length === 0 ? (
                    <div className="flex flex-col items-center justify-center p-8 mt-20 opacity-50 space-y-4">
                        <div className="bg-primary/10 p-4 rounded-full">
                            <div className="h-8 w-8 bg-primary rounded-full animate-pulse" />
                        </div>
                        <h2 className="text-2xl font-semibold">무엇을 도와드릴까요?</h2>
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
                                        "px-4 py-2.5 max-w-[80%] rounded-2xl",
                                        msg.role === 'user'
                                            ? "bg-secondary text-secondary-foreground rounded-tr-sm whitespace-pre-wrap"
                                            : "bg-transparent text-foreground border rounded-tl-sm"
                                    )}
                                >
                                    {msg.role === 'assistant' ? (
                                        <MarkdownContent content={msg.content} />
                                    ) : (
                                        msg.content
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
                />
            </div>
        </div>
    )
}
