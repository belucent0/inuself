"use client"

import * as React from "react"
import { Badge } from "@/components/ui/badge"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { cn } from "@/lib/utils"

interface SearchSource {
    position: number
    title: string
    url: string
    snippet: string
    engine: string
}

function GlobeIcon({ className }: { className?: string }) {
    return (
        <svg
            className={className}
            fill="none"
            height="24"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            viewBox="0 0 24 24"
            width="24"
            xmlns="http://www.w3.org/2000/svg"
        >
            <circle cx="12" cy="12" r="10" />
            <line x1="2" x2="22" y1="12" y2="12" />
            <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
        </svg>
    )
}

function SourceCard({ source }: { source: SearchSource }) {
    return (
        <a
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex flex-col justify-between p-3 rounded-xl border bg-card hover:bg-muted/50 transition-all h-24 min-w-[160px] w-[160px] group relative overflow-hidden shrink-0 snap-start"
        >
            <div className="flex items-start justify-between gap-2">
                <span className="text-xs font-medium line-clamp-2 leading-tight group-hover:text-primary transition-colors">
                    {source.title}
                </span>
                <Badge variant="secondary" className="text-[10px] h-5 px-1.5 shrink-0 bg-muted-foreground/10">
                    {source.position}
                </Badge>
            </div>
            <div className="flex items-center gap-1 mt-auto">
                <div className="w-4 h-4 rounded-full bg-muted flex items-center justify-center shrink-0">
                    <GlobeIcon className="w-2.5 h-2.5 text-muted-foreground" />
                </div>
                <span className="text-[10px] text-muted-foreground truncate max-w-[80%]">
                    {new URL(source.url).hostname}
                </span>
            </div>
            <div className="absolute inset-0 border-2 border-primary/0 group-hover:border-primary/10 rounded-xl transition-all pointer-events-none" />
        </a>
    )
}

export function SourceCarousel({ sources }: { sources: SearchSource[] }) {
    const scrollRef = React.useRef<HTMLDivElement>(null)
    const [canScrollLeft, setCanScrollLeft] = React.useState(false)
    const [canScrollRight, setCanScrollRight] = React.useState(true)

    const checkScroll = () => {
        if (scrollRef.current) {
            const { scrollLeft, scrollWidth, clientWidth } = scrollRef.current
            setCanScrollLeft(scrollLeft > 0)
            setCanScrollRight(Math.ceil(scrollLeft) < scrollWidth - clientWidth - 1)
        }
    }

    React.useEffect(() => {
        checkScroll()
        window.addEventListener('resize', checkScroll)
        return () => window.removeEventListener('resize', checkScroll)
    }, [sources])

    const scroll = (direction: 'left' | 'right') => {
        if (scrollRef.current) {
            // 한 화면 너비만큼 이동 (페이징 효과)
            const scrollAmount = scrollRef.current.clientWidth
            scrollRef.current.scrollBy({
                left: direction === 'left' ? -scrollAmount : scrollAmount,
                behavior: 'smooth'
            })
        }
    }

    if (!sources || sources.length === 0) return null

    return (
        <div className="relative group/carousel">
            {/* 좌측 화살표 */}
            {canScrollLeft && (
                <button 
                    onClick={() => scroll('left')}
                    className="absolute -left-3 top-1/2 -translate-y-1/2 z-10 p-1.5 bg-background/90 backdrop-blur-sm border rounded-full shadow-md hover:bg-muted text-muted-foreground hover:text-foreground transition-all opacity-0 group-hover/carousel:opacity-100"
                    aria-label="Scroll left"
                >
                    <ChevronLeft className="h-4 w-4" />
                </button>
            )}
            
            {/* 우측 화살표 */}
            {canScrollRight && (
                <button 
                    onClick={() => scroll('right')}
                    className="absolute -right-3 top-1/2 -translate-y-1/2 z-10 p-1.5 bg-background/90 backdrop-blur-sm border rounded-full shadow-md hover:bg-muted text-muted-foreground hover:text-foreground transition-all opacity-0 group-hover/carousel:opacity-100"
                    aria-label="Scroll right"
                >
                    <ChevronRight className="h-4 w-4" />
                </button>
            )}

            <div 
                ref={scrollRef}
                onScroll={checkScroll}
                className="flex gap-3 overflow-x-auto pb-2 px-1 snap-x"
                style={{ 
                    scrollbarWidth: 'none',  /* Firefox */
                    msOverflowStyle: 'none'  /* IE and Edge */
                }} 
            >
                <style jsx>{`
                    div::-webkit-scrollbar {
                        display: none;
                    }
                `}</style>
                {sources.map((source) => (
                    <SourceCard key={source.position} source={source} />
                ))}
            </div>
            
            {/* 우측 페이드 효과 (더 있다는 힌트) - 우측 스크롤 가능할 때만 표시 */}
            {canScrollRight && (
                <div className="absolute right-0 top-0 bottom-2 w-16 bg-gradient-to-l from-background to-transparent pointer-events-none transition-opacity duration-300" />
            )}
        </div>
    )
}
