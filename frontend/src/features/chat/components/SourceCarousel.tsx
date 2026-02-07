/**
 * 검색 소스 캐러셀 컴포넌트
 */

import { useRef, useState, useEffect } from 'react'
import { ChevronLeft, ChevronRight, Globe, Database, FileText } from 'lucide-react'
import { Badge } from '@/shared/components/ui/badge'
import { cn } from '@/shared/utils/cn'
import type { SearchSource } from '../types'

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
  const isRag = source.source === 'rag'
  const isInternalUrl = source.url.startsWith('/contents/')
  const target = isInternalUrl ? '_self' : '_blank'

  return (
    <a
      href={source.url}
      target={target}
      rel="noopener noreferrer"
      className={cn(
        'flex flex-col justify-between p-3 rounded-xl border bg-card hover:bg-muted/50 transition-all h-24 min-w-[160px] w-[160px] group relative overflow-hidden shrink-0 snap-start',
        isRag ? 'border-green-500/30 hover:border-green-500/50' : 'border-border'
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span
          className={cn(
            'text-xs font-medium line-clamp-2 leading-tight group-hover:text-primary transition-colors',
            isRag && 'group-hover:text-green-600'
          )}
        >
          {source.title}
        </span>
        <Badge
          variant="secondary"
          className={cn(
            'text-[10px] h-5 px-1.5 shrink-0',
            isRag ? 'bg-green-500/10 text-green-600' : 'bg-muted-foreground/10'
          )}
        >
          {source.position}
        </Badge>
      </div>
      <div className="flex items-center gap-1 mt-auto">
        <div
          className={cn(
            'w-4 h-4 rounded-full flex items-center justify-center shrink-0',
            isRag ? 'bg-green-500/10' : 'bg-muted'
          )}
        >
          {isRag ? (
            <FileText className="w-2.5 h-2.5 text-green-600" />
          ) : (
            <GlobeIcon className="w-2.5 h-2.5 text-muted-foreground" />
          )}
        </div>
        <span
          className={cn(
            'text-[10px] truncate max-w-[80%]',
            isRag ? 'text-green-600/70' : 'text-muted-foreground'
          )}
        >
          {isRag
            ? '내 문서'
            : (() => {
                try {
                  return new URL(source.url).hostname
                } catch {
                  return source.url
                }
              })()}
        </span>
      </div>
      <div
        className={cn(
          'absolute inset-0 border-2 rounded-xl transition-all pointer-events-none',
          isRag
            ? 'border-green-500/0 group-hover:border-green-500/20'
            : 'border-primary/0 group-hover:border-primary/10'
        )}
      />

      {isRag && (
        <div className="absolute top-1 right-1">
          <Database className="w-3 h-3 text-green-500/50" />
        </div>
      )}
    </a>
  )
}

export function SourceCarousel({ sources }: { sources: SearchSource[] }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [canScrollLeft, setCanScrollLeft] = useState(false)
  const [canScrollRight, setCanScrollRight] = useState(true)
  const [, setForceUpdate] = useState({})

  // Force update when sources change to ensure layout recalculation
  useEffect(() => {
    setForceUpdate({})
  }, [sources])

  const webSources = sources.filter((s) => s.source !== 'rag')
  const ragSources = sources.filter((s) => s.source === 'rag')

  const checkScroll = () => {
    if (scrollRef.current) {
      const { scrollLeft, scrollWidth, clientWidth } = scrollRef.current
      setCanScrollLeft(scrollLeft > 0)
      setCanScrollRight(Math.ceil(scrollLeft) < scrollWidth - clientWidth - 1)
    }
  }

  useEffect(() => {
    checkScroll()
    window.addEventListener('resize', checkScroll)
    return () => window.removeEventListener('resize', checkScroll)
  }, [sources])

  const scroll = (direction: 'left' | 'right') => {
    if (scrollRef.current) {
      const scrollAmount = scrollRef.current.clientWidth
      scrollRef.current.scrollBy({
        left: direction === 'left' ? -scrollAmount : scrollAmount,
        behavior: 'smooth',
      })
      setTimeout(checkScroll, 300) // Recheck after scroll animation
    }
  }

  if (!sources || sources.length === 0) return null

  const hasMixedSources = webSources.length > 0 && ragSources.length > 0

  return (
    <div className="relative group/carousel">
      {canScrollLeft && (
        <button
          onClick={() => scroll('left')}
          className="absolute -left-3 top-1/2 -translate-y-1/2 z-10 p-1.5 bg-background/90 backdrop-blur-sm border rounded-full shadow-md hover:bg-muted text-muted-foreground hover:text-foreground transition-all opacity-0 group-hover/carousel:opacity-100"
          aria-label="Scroll left"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
      )}

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
        className="flex gap-3 overflow-x-auto pb-2 px-1 snap-x scrollbar-hide"
        style={{
          scrollbarWidth: 'none',
          msOverflowStyle: 'none',
        }}
      >
        {webSources.length > 0 && (
          <>
            {hasMixedSources && (
               <div className="flex flex-col justify-center items-center gap-1 px-2 py-1 bg-blue-500/10 rounded-lg shrink-0 w-[40px] h-24">
                <Globe className="h-4 w-4 text-blue-500" />
                <span className="text-[10px] font-medium text-blue-500 rotate-90 whitespace-nowrap">웹</span>
              </div>
            )}
            {webSources.map((source) => (
              <SourceCard key={`web-${source.position}`} source={source} />
            ))}
          </>
        )}

        {hasMixedSources && (
          <div className="w-px h-20 self-center bg-border shrink-0 mx-1" />
        )}

        {ragSources.length > 0 && (
          <>
            {hasMixedSources && (
               <div className="flex flex-col justify-center items-center gap-1 px-2 py-1 bg-green-500/10 rounded-lg shrink-0 w-[40px] h-24">
                <Database className="h-4 w-4 text-green-500" />
                <span className="text-[10px] font-medium text-green-500 rotate-90 whitespace-nowrap">문서</span>
              </div>
            )}
            {ragSources.map((source) => (
              <SourceCard key={`rag-${source.position}`} source={source} />
            ))}
          </>
        )}
      </div>

      {canScrollRight && (
        <div className="absolute right-0 top-0 bottom-2 w-16 bg-gradient-to-l from-background to-transparent pointer-events-none transition-opacity duration-300" />
      )}
    </div>
  )
}
