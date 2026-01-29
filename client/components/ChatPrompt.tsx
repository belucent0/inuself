"use client"

import * as React from "react"
import { useState } from "react"
import { Send, Paperclip, Youtube } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import { YouTubeLinkModal } from "./YouTubeLinkModal"
import { uploadYouTubeContent } from "@/lib/api"
import { AIModeSelector, AI_MODE_CONFIG, type AIMode } from "@/components/AIModeSelector"

interface ChatPromptProps extends React.HTMLAttributes<HTMLDivElement> {
    input: string
    onInputChange: (value: string) => void
    onSendMessage: (message: string) => void
    isLoading?: boolean
    onFileUpload?: () => void
    mode?: AIMode
    onModeChange?: (mode: AIMode) => void
    messages?: any[]
}

export function ChatPrompt({
    className,
    input,
    onInputChange,
    onSendMessage,
    isLoading,
    onFileUpload,
    mode = 'search',
    onModeChange,
    messages = [],
    ...props
}: ChatPromptProps) {
    const textareaRef = React.useRef<HTMLTextAreaElement>(null)
    const [youtubeModalOpen, setYoutubeModalOpen] = useState(false)

    const handleYouTubeSubmit = async (url: string) => {
        const result = await uploadYouTubeContent(url)
        toast.success("YouTube 영상이 처리 대기열에 추가되었습니다", {
            description: "콘텐츠 목록에서 진행 상황을 확인할 수 있습니다.",
        })
    }

    const adjustHeight = () => {
        const textarea = textareaRef.current
        if (textarea) {
            textarea.style.height = "auto"
            textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
        }
    }

    const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        onInputChange(e.target.value)
        adjustHeight()
    }

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault()
            handleSubmit()
        }
    }

    const handleSubmit = () => {
        const inputValue = input ?? ''
        if (!inputValue.trim() || isLoading) return
        onSendMessage(inputValue)
        if (textareaRef.current) {
            textareaRef.current.style.height = "auto"
        }
    }

    const currentConfig = AI_MODE_CONFIG[mode]
    const inputValue = input ?? ''
    const isDisabled = !inputValue.trim() || (isLoading ?? false)

    // 모드별 placeholder
    const placeholders: Record<AIMode, string> = {
        simple: "무엇이든 물어보세요...",
        search: "웹에서 검색할 내용을 입력하세요...",
        rag: "내 문서에서 검색할 내용을 입력하세요...",
        reasoning: "분석이 필요한 질문을 입력하세요...",
        hybrid: "웹과 내 문서에서 통합 검색할 내용을 입력하세요..."
    }

    return (
        <div className={cn("relative w-full max-w-3xl mx-auto", className)} {...props}>
            <div className={cn(
                "relative flex flex-col w-full p-3 rounded-xl border transition-all",
                currentConfig.bgColor,
                "border-border/50 focus-within:ring-1 focus-within:border-opacity-50",
                mode === 'search' && "focus-within:ring-blue-500/50 focus-within:border-blue-500/50",
                mode === 'rag' && "focus-within:ring-green-500/50 focus-within:border-green-500/50",
                mode === 'reasoning' && "focus-within:ring-purple-500/50 focus-within:border-purple-500/50",
                mode === 'hybrid' && "focus-within:ring-amber-500/50 focus-within:border-amber-500/50",
                mode === 'simple' && "focus-within:ring-slate-500/50 focus-within:border-slate-500/50"
            )}>
                <textarea
                    ref={textareaRef}
                    value={inputValue}
                    onChange={handleChange}
                    onKeyDown={handleKeyDown}
                    placeholder={placeholders[mode]}
                    disabled={isLoading ?? false}
                    className="w-full resize-none bg-transparent border-0 focus-visible:ring-0 p-1 min-h-[44px] max-h-[200px] text-sm md:text-base leading-relaxed scrollbar-thin scrollbar-thumb-muted-foreground/20"
                    rows={1}
                />

                <div className="flex justify-between items-end mt-2">
                    <div className="flex items-center gap-1">
                        {/* AI 모드 선택 */}
                        <AIModeSelector
                            mode={mode}
                            onModeChange={(newMode) => {
                                onModeChange?.(newMode)
                                toast.info(`${AI_MODE_CONFIG[newMode].label} 모드`, {
                                    description: AI_MODE_CONFIG[newMode].description,
                                    duration: 2000,
                                })
                            }}
                            disabled={isLoading}
                            compact
                        />

                        <div className="mx-1 h-4 w-[1px] bg-border" />

                        <TooltipProvider>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button type="button" variant="ghost" size="icon" className="h-8 w-8 rounded-full text-muted-foreground hover:text-foreground" onClick={onFileUpload}>
                                        <Paperclip className="h-4 w-4" />
                                        <span className="sr-only">파일 첨부</span>
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>파일 첨부</TooltipContent>
                            </Tooltip>
                        </TooltipProvider>

                        <TooltipProvider>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="h-8 w-8 rounded-full text-muted-foreground hover:text-red-500"
                                        onClick={() => setYoutubeModalOpen(true)}
                                    >
                                        <Youtube className="h-4 w-4" />
                                        <span className="sr-only">YouTube 링크</span>
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>YouTube 링크로 콘텐츠 생성</TooltipContent>
                            </Tooltip>
                        </TooltipProvider>
                    </div>

                    <div className="flex items-center gap-2">
                        <TooltipProvider>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button
                                        type="button"
                                        size="icon"
                                        onClick={handleSubmit}
                                        disabled={isDisabled}
                                        className={cn(
                                            "h-8 w-8 rounded-full transition-all",
                                            inputValue.trim()
                                                ? cn("text-white", currentConfig.color.replace('text-', 'bg-').replace('/500', '-500'))
                                                : "bg-muted text-muted-foreground"
                                        )}
                                    >
                                        <Send className="h-4 w-4" />
                                        <span className="sr-only">전송</span>
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>전송</TooltipContent>
                            </Tooltip>
                        </TooltipProvider>
                    </div>
                </div>
            </div>

            {messages.length === 0 && (
                <div className="text-center mt-2 text-xs text-muted-foreground animate-in fade-in slide-in-from-bottom-2">
                    {currentConfig.description}
                </div>
            )}

            <YouTubeLinkModal
                open={youtubeModalOpen}
                onOpenChange={setYoutubeModalOpen}
                onSubmit={handleYouTubeSubmit}
            />
        </div>
    )
}
