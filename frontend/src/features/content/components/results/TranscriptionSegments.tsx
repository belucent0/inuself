/**
 * 전사 세그먼트 목록
 * - 화자 배지 + 타임스탬프 클릭 → 시크
 * - timeupdate 이벤트 → 현재 세그먼트 하이라이트
 * - 자동 스크롤 토글
 */

import { useState, useEffect, useRef, type RefObject } from 'react'
import type { TranscriptionSegment } from '../../types'
import { Badge } from '@/shared/components/ui/badge'
import { Switch } from '@/shared/components/ui/switch'
import { Label } from '@/shared/components/ui/label'
import { Captions } from 'lucide-react'
import { cn } from '@/shared/utils/cn'

interface TranscriptionSegmentsProps {
  segments: TranscriptionSegment[]
  speakers: string[]
  mediaRef: RefObject<HTMLMediaElement | null>
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

const SPEAKER_COLORS = [
  'bg-blue-500/10 text-blue-700 dark:text-blue-400',
  'bg-green-500/10 text-green-700 dark:text-green-400',
  'bg-purple-500/10 text-purple-700 dark:text-purple-400',
  'bg-orange-500/10 text-orange-700 dark:text-orange-400',
  'bg-pink-500/10 text-pink-700 dark:text-pink-400',
  'bg-cyan-500/10 text-cyan-700 dark:text-cyan-400',
]

export function TranscriptionSegments({
  segments,
  speakers,
  mediaRef,
}: TranscriptionSegmentsProps) {
  const [currentSegmentId, setCurrentSegmentId] = useState<number | null>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const segmentRefs = useRef<Map<number, HTMLDivElement>>(new Map())

  const speakerColorMap = new Map<string, string>()
    ; (speakers || []).forEach((speaker, i) => {
      speakerColorMap.set(speaker, SPEAKER_COLORS[i % SPEAKER_COLORS.length])
    })

  useEffect(() => {
    const media = mediaRef.current
    if (!media) return

    const handleTimeUpdate = () => {
      const currentTime = media.currentTime
      const current = segments.find(
        (s) => currentTime >= s.start && currentTime < s.end
      )
      if (current) {
        setCurrentSegmentId(current.id)
      }
    }

    media.addEventListener('timeupdate', handleTimeUpdate)
    return () => media.removeEventListener('timeupdate', handleTimeUpdate)
  }, [mediaRef, segments])

  useEffect(() => {
    if (!autoScroll || currentSegmentId === null) return
    const el = segmentRefs.current.get(currentSegmentId)
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [currentSegmentId, autoScroll])

  const handleSeek = (startTime: number) => {
    const media = mediaRef.current
    if (!media) return
    media.currentTime = startTime
    media.play().catch(() => { })
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* 헤더: 고정 */}
      <div className="shrink-0 flex items-center justify-between px-4 py-2 border-b">
        <Badge variant="default" className="gap-1 text-xs font-medium">
          <Captions className="h-4 w-4" />
          스크립트
        </Badge>
        <div className="flex items-center gap-2">
          <Label htmlFor="auto-scroll" className="text-xs text-muted-foreground">
            자동 스크롤
          </Label>
          <Switch
            id="auto-scroll"
            checked={autoScroll}
            onCheckedChange={setAutoScroll}
            className="scale-75"
          />
        </div>
      </div>

      {/* 세그먼트: 타임라인 스크롤 */}
      <div className="flex-1 overflow-y-auto p-3">
        <div className="space-y-0">
          {segments.map((segment) => (
            <div
              key={segment.id}
              ref={(el) => {
                if (el) segmentRefs.current.set(segment.id, el)
              }}
              className="flex gap-1"
            >
              {/* 타임라인: 시간 + 세로선 */}
              <div className="flex flex-col items-center shrink-0 w-10">
                <div className="h-1.5 w-px bg-border" />
                <button
                  onClick={() => handleSeek(segment.start)}
                  className="text-[14px] text-muted-foreground hover:text-primary font-mono h-8 flex items-center"
                >
                  {formatTime(segment.start)}
                </button>
                <div className="flex-1 w-px bg-border" />
              </div>

              {/* 콘텐츠 */}
              <div
                className={cn(
                  'flex-1 min-w-0 pb-3 mt-1.5 rounded-lg px-2 py-1 transition-colors',
                  currentSegmentId === segment.id
                    ? 'bg-primary/20 ring-1 ring-primary/30'
                    : 'hover:bg-muted/50'
                )}
              >
                {segment.speaker && (
                  <Badge
                    variant="outline"
                    className={cn(
                      'text-xs h-5 mb-1',
                      speakerColorMap.get(segment.speaker)
                    )}
                  >
                    {segment.speaker}
                  </Badge>
                )}
                <p className="text-base leading-relaxed">{segment.text}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
