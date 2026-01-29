"use client"

import * as React from "react"
import { ChevronDown, ChevronUp, Brain, Search, CheckCircle2, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

interface ThinkingStep {
    step: string
    content: string
    timestamp?: number
}

interface ThinkingProcessAccordionProps {
    steps?: ThinkingStep[]
    content?: string  // 레거시 지원
    isStreaming?: boolean
}

// 단계별 아이콘
function getStepIcon(step: string, isLatest: boolean, isStreaming: boolean) {
    if (isLatest && isStreaming) {
        return <Loader2 className="h-3 w-3 animate-spin text-purple-500" />
    }

    switch (step) {
        case 'intent_analysis':
        case 'intent_result':
            return <Brain className="h-3 w-3 text-purple-500" />
        case 'web_search':
        case 'web_search_complete':
        case 'rag_search':
        case 'rag_search_complete':
        case 'search_complete':
            return <Search className="h-3 w-3 text-blue-500" />
        case 'reasoning_start':
        case 'reasoning_step':
        case 'reasoning_complete':
            return <Brain className="h-3 w-3 text-purple-500" />
        case 'generation_start':
        case 'generation_complete':
            return <CheckCircle2 className="h-3 w-3 text-emerald-500" />
        case 'reflection_start':
        case 'reflection_complete':
            return <CheckCircle2 className="h-3 w-3 text-green-500" />
        default:
            return <CheckCircle2 className="h-3 w-3 text-muted-foreground" />
    }
}

// 단계 이름 변환
function getStepLabel(step: string): string {
    const labels: Record<string, string> = {
        'intent_analysis': '의도 분석',
        'intent_result': '모드 결정',
        'web_search': '웹 검색 중',
        'web_search_complete': '웹 검색 완료',
        'rag_search': '문서 검색 중',
        'rag_search_complete': '문서 검색 완료',
        'search_complete': '통합 검색 완료',
        'reasoning_start': '추론 시작',
        'reasoning_step': '추론 중',
        'reasoning_complete': '추론 완료',
        'generation_start': '답변 생성 중',
        'generation_complete': '답변 생성 완료',
        'reflection_start': '검증 시작',
        'reflection_complete': '검증 완료',
    }
    return labels[step] || step
}

export function ThinkingProcessAccordion({ steps, content, isStreaming }: ThinkingProcessAccordionProps) {
    const [isOpen, setIsOpen] = React.useState(true)
    const [hasAutoCollapsed, setHasAutoCollapsed] = React.useState(false)

    // 레거시 content 지원
    const thinkingSteps = steps || (content ? [{ step: 'thinking', content, timestamp: Date.now() }] : [])

    // 답변 생성 시작 시 자동으로 접기
    React.useEffect(() => {
        if (hasAutoCollapsed) return

        const hasGenerationStarted = thinkingSteps.some(
            step => step.step === 'generation_start' || step.step === 'generation_complete'
        )

        if (hasGenerationStarted) {
            setIsOpen(false)
            setHasAutoCollapsed(true)
        }
    }, [thinkingSteps, hasAutoCollapsed])

    if (thinkingSteps.length === 0) return null

    return (
        <div className="mb-4 rounded-lg border bg-muted/30 overflow-hidden transition-all animate-in fade-in slide-in-from-top-2 duration-300">
            <button
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center justify-between w-full px-4 py-2.5 text-sm font-medium text-muted-foreground hover:bg-muted/50 transition-colors group"
            >
                <div className="flex items-center gap-2">
                    <div className={cn(
                        "p-1 rounded-md bg-muted/50 group-hover:bg-muted transition-colors",
                        isStreaming && "animate-pulse"
                    )}>
                        <Brain className="h-4 w-4 text-purple-500/70" />
                    </div>
                    <span>
                        사고 과정
                        {isStreaming && <span className="animate-pulse ml-1">...</span>}
                        {!isStreaming && thinkingSteps.length > 0 && (
                            <span className="text-xs ml-2 text-muted-foreground/60">
                                ({thinkingSteps.length}단계)
                            </span>
                        )}
                    </span>
                </div>
                {isOpen ? (
                    <ChevronUp className="h-4 w-4 opacity-50 group-hover:opacity-100 transition-opacity" />
                ) : (
                    <ChevronDown className="h-4 w-4 opacity-50 group-hover:opacity-100 transition-opacity" />
                )}
            </button>

            {isOpen && (
                <div className="px-4 py-3 bg-muted/10 border-t">
                    <div className="space-y-2">
                        {thinkingSteps.map((step, index) => {
                            const isLatest = index === thinkingSteps.length - 1
                            return (
                                <div
                                    key={`${step.step}-${index}`}
                                    className={cn(
                                        "flex items-start gap-3 text-sm animate-in fade-in slide-in-from-left-2",
                                        isLatest && isStreaming && "opacity-80"
                                    )}
                                    style={{ animationDelay: `${index * 50}ms` }}
                                >
                                    {/* 타임라인 */}
                                    <div className="flex flex-col items-center">
                                        <div className={cn(
                                            "p-1 rounded-full bg-background border",
                                            isLatest && isStreaming && "border-purple-500/50"
                                        )}>
                                            {getStepIcon(step.step, isLatest, isStreaming || false)}
                                        </div>
                                        {index < thinkingSteps.length - 1 && (
                                            <div className="w-px h-full min-h-[16px] bg-border" />
                                        )}
                                    </div>

                                    {/* 내용 */}
                                    <div className="flex-1 pb-2">
                                        <div className="flex items-center gap-2">
                                            <span className="font-medium text-foreground/80">
                                                {getStepLabel(step.step)}
                                            </span>
                                        </div>
                                        <p className="text-muted-foreground text-xs mt-0.5 leading-relaxed">
                                            {step.content}
                                        </p>
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                </div>
            )}
        </div>
    )
}
