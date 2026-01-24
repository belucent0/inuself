"use client"

import * as React from "react"
import { ChevronDown, ChevronUp, Brain } from "lucide-react"
import { cn } from "@/lib/utils"

interface ThinkingProcessAccordionProps {
    content: string
    isStreaming?: boolean
}

export function ThinkingProcessAccordion({ content, isStreaming }: ThinkingProcessAccordionProps) {
    const [isOpen, setIsOpen] = React.useState(true)

    // 내용이 없으면 렌더링하지 않음
    if (!content) return null

    return (
        <div className="mb-4 rounded-lg border bg-muted/30 overflow-hidden transition-all animate-in fade-in slide-in-from-top-2 duration-300">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center justify-between w-full px-4 py-2.5 text-sm font-medium text-muted-foreground hover:bg-muted/50 transition-colors group"
            >
                <div className="flex items-center gap-2">
                    <div className={cn("p-1 rounded-md bg-muted/50 group-hover:bg-muted transition-colors", isStreaming && "animate-pulse")}>
                        <Brain className="h-4 w-4 text-purple-500/70" />
                    </div>
                    <span>생각하는 과정 {isStreaming && <span className="animate-pulse">...</span>}</span>
                </div>
                {isOpen ? (
                    <ChevronUp className="h-4 w-4 opacity-50 group-hover:opacity-100 transition-opacity" />
                ) : (
                    <ChevronDown className="h-4 w-4 opacity-50 group-hover:opacity-100 transition-opacity" />
                )}
            </button>
            
            {isOpen && (
                <div className="px-4 py-3 text-sm text-muted-foreground bg-muted/10 border-t prose dark:prose-invert max-w-none">
                    <div className="whitespace-pre-wrap leading-relaxed opacity-90 font-light tracking-wide text-[13px] border-l-2 border-purple-500/20 pl-3">
                        {content}
                    </div>
                </div>
            )}
        </div>
    )
}
