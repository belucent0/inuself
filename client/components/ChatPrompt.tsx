"use client"

import * as React from "react"
import { useState } from "react"
import { Send, Paperclip, Globe, Youtube } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { toast } from "sonner"
import { YouTubeLinkModal } from "./YouTubeLinkModal"
import { uploadYouTubeContent } from "@/lib/api"

interface ChatPromptProps extends React.HTMLAttributes<HTMLDivElement> {
    input: string
    onInputChange: (value: string) => void
    onSendMessage: (message: string) => void
    isLoading?: boolean
    onFileUpload?: () => void
}

export function ChatPrompt({
    className,
    input,
    onInputChange,
    onSendMessage,
    isLoading,
    onFileUpload,
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

    const inputValue = input ?? ''
    const isDisabled = !inputValue.trim() || (isLoading ?? false)

    return (
        <div className={cn("relative w-full max-w-3xl mx-auto", className)} {...props}>
            <div className="relative flex flex-col w-full p-3 bg-secondary/50 rounded-xl border focus-within:ring-1 focus-within:ring-ring focus-within:border-ring transition-all">
                <textarea
                    ref={textareaRef}
                    value={inputValue}
                    onChange={handleChange}
                    onKeyDown={handleKeyDown}
                    placeholder="무엇이든 물어보세요..."
                    disabled={isLoading ?? false}
                    className="w-full resize-none bg-transparent border-0 focus-visible:ring-0 p-1 min-h-[44px] max-h-[200px] text-sm md:text-base leading-relaxed scrollbar-thin scrollbar-thumb-muted-foreground/20"
                    rows={1}
                />

                <div className="flex justify-between items-end mt-2">
                    <div className="flex items-center gap-1">
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
                                    <Button type="button" variant="ghost" size="icon" className="h-8 w-8 rounded-full text-muted-foreground hover:text-foreground">
                                        <Globe className="h-4 w-4" />
                                        <span className="sr-only">웹 검색</span>
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent>웹 검색</TooltipContent>
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
                                        inputValue.trim() ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"
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

            <div className="text-center mt-2 text-xs text-muted-foreground">
                AI는 실수를 할 수 있습니다. 중요한 정보는 확인해 주세요.
            </div>

            <YouTubeLinkModal
                open={youtubeModalOpen}
                onOpenChange={setYoutubeModalOpen}
                onSubmit={handleYouTubeSubmit}
            />
        </div>
    )
}
