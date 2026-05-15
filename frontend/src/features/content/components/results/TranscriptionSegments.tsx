/**
 * 전사 세그먼트 목록
 * - 화자 배지 + 타임스탬프 클릭 → 시크
 * - timeupdate 이벤트 → 현재 세그먼트 하이라이트
 * - 자동 스크롤 토글
 * - PR-Translate.1: 보기 모드 토글 (원문/한국어/양쪽) + 수동 번역 트리거
 */

import { useState, useEffect, useRef, useMemo, type RefObject } from 'react'
import { useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'
import type { TranscriptionSegment } from '../../types'
import { Badge } from '@/shared/components/ui/badge'
import { Switch } from '@/shared/components/ui/switch'
import { Label } from '@/shared/components/ui/label'
import { Button } from '@/shared/components/ui/button'
import { Captions, Languages, Loader2 } from 'lucide-react'
import { cn } from '@/shared/utils/cn'
import { contentsApi } from '@/shared/services/endpoints/contents'

type ViewMode = 'original' | 'translated' | 'both'

interface TranscriptionSegmentsProps {
  segments: TranscriptionSegment[]
  speakers: string[]
  mediaRef: RefObject<HTMLMediaElement | null>
  contentId: string
}

function formatTime(seconds: number): string {
  const hrs = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)
  return `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}

const SPEAKER_COLORS = [
  'bg-blue-500/10 text-blue-700 dark:text-blue-400',
  'bg-green-500/10 text-green-700 dark:text-green-400',
  'bg-purple-500/10 text-purple-700 dark:text-purple-400',
  'bg-orange-500/10 text-orange-700 dark:text-orange-400',
  'bg-pink-500/10 text-pink-700 dark:text-pink-400',
  'bg-cyan-500/10 text-cyan-700 dark:text-cyan-400',
]

const VIEW_MODES: { value: ViewMode; label: string }[] = [
  { value: 'original', label: '원문' },
  { value: 'translated', label: '한국어' },
  { value: 'both', label: '양쪽' },
]

export function TranscriptionSegments({
  segments,
  speakers,
  mediaRef,
  contentId,
}: TranscriptionSegmentsProps) {
  const [currentSegmentId, setCurrentSegmentId] = useState<number | null>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const [viewMode, setViewMode] = useState<ViewMode>('original')
  const segmentRefs = useRef<Map<number, HTMLDivElement>>(new Map())

  const translateMutation = useMutation({
    mutationFn: () => contentsApi.translateContent(contentId, 'ko'),
    onSuccess: () => {
      // endpoint는 즉시 "accepted" 응답. 실제 진행은 BG + SSE invalidate로 갱신.
      toast.info('번역 시작 — 청크가 완료되는 대로 표시됩니다')
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : '번역 실패'
      toast.error(`번역 시작 실패: ${msg}`)
    },
  })

  const translatedCount = useMemo(
    () => segments.filter((s) => !!s.translation_ko).length,
    [segments]
  )
  const hasAnyTranslation = translatedCount > 0
  const allTranslated = translatedCount === segments.length

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

  const showOriginal = viewMode === 'original' || viewMode === 'both'
  const showTranslated = viewMode === 'translated' || viewMode === 'both'
  const isTranslating = translateMutation.isPending

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* 헤더: 고정 */}
      <div className="shrink-0 flex items-center justify-between px-4 py-2 border-b gap-2 flex-wrap">
        <Badge variant="default" className="gap-1 text-xs font-medium">
          <Captions className="h-4 w-4" />
          스크립트
        </Badge>
        <div className="flex items-center gap-3 flex-wrap">
          {/* 보기 모드 토글 */}
          {hasAnyTranslation && (
            <div className="inline-flex rounded-md border bg-background p-0.5 text-xs">
              {VIEW_MODES.map((m) => (
                <button
                  key={m.value}
                  onClick={() => setViewMode(m.value)}
                  className={cn(
                    'px-2.5 py-0.5 rounded transition-colors',
                    viewMode === m.value
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  {m.label}
                </button>
              ))}
            </div>
          )}
          {/* 번역 시작/추가 번역 버튼 */}
          {!allTranslated && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={() => translateMutation.mutate()}
              disabled={isTranslating}
              title="transcript를 한국어로 번역합니다"
            >
              {isTranslating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Languages className="h-3.5 w-3.5" />
              )}
              {hasAnyTranslation ? '미번역 청크 번역' : '한국어 번역'}
            </Button>
          )}
          {allTranslated && !isTranslating && (
            <Badge variant="outline" className="text-xs gap-1">
              <Languages className="h-3 w-3" />
              번역 완료
            </Badge>
          )}
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

      {/* 진행 상황 안내 */}
      {isTranslating && (
        <div className="shrink-0 px-4 py-1.5 text-xs text-muted-foreground bg-muted/30 border-b">
          번역 중 — 청크가 완료되는 대로 표시됩니다 ({translatedCount}/{segments.length} 세그먼트)
        </div>
      )}

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
              <div className="flex flex-col items-center shrink-0 w-14">
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
                {showOriginal && (
                  <p className="text-base leading-relaxed">{segment.text}</p>
                )}
                {showTranslated && segment.translation_ko && (
                  <p
                    className={cn(
                      'text-base leading-relaxed',
                      showOriginal && 'mt-1 text-muted-foreground'
                    )}
                  >
                    {segment.translation_ko}
                  </p>
                )}
                {showTranslated && !segment.translation_ko && !showOriginal && (
                  <p className="text-sm text-muted-foreground italic">
                    (미번역)
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
