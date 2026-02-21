/**
 * 소스 옵션 패널
 * 콘텐츠 채팅 시 요약/전사/화자 필터를 선택하는 체크박스 패널
 */

import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Label } from '@/shared/components/ui/label'
import { cn } from '@/shared/utils/cn'
import type { ContentDetail } from '../types'

export interface SourceOptions {
  include_summary: boolean
  include_transcription: boolean
  speaker_filter: string[] | null  // null = 전체
}

interface SourceOptionsPanelProps {
  content: ContentDetail
  options: SourceOptions
  onChange: (options: SourceOptions) => void
}

export function SourceOptionsPanel({
  content,
  options,
  onChange,
}: SourceOptionsPanelProps) {
  const [expanded, setExpanded] = useState(false)

  const hasSummary = !!content.summary_md
  const speakers: string[] = content.transcription?.speakers ?? []
  const hasTranscription = speakers.length > 0 || (
    content.content_type === 'AUDIO' &&
    !!content.transcription?.segments?.length
  )

  // 요약도 전사도 없으면 패널 숨김
  if (!hasSummary && !hasTranscription) return null

  const handleSummaryChange = (checked: boolean) => {
    onChange({ ...options, include_summary: checked })
  }

  const handleTranscriptionChange = (checked: boolean) => {
    onChange({
      ...options,
      include_transcription: checked,
      // 전사 OFF 시 화자 필터도 초기화
      speaker_filter: checked ? options.speaker_filter : null,
    })
  }

  const handleSpeakerChange = (speaker: string, checked: boolean) => {
    const current = options.speaker_filter ?? speakers
    const next = checked
      ? [...current, speaker]
      : current.filter((s) => s !== speaker)
    // 전체 선택이면 null(필터 없음)로
    onChange({
      ...options,
      speaker_filter: next.length === speakers.length ? null : next,
    })
  }

  const activeSpeakers = options.speaker_filter ?? speakers

  return (
    <div className="border-b bg-muted/30">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 w-full px-3 py-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0" />
        )}
        소스 설정
      </button>

      {expanded && (
        <div className="px-4 pb-3 space-y-2">
          {hasSummary && (
            <div className="flex items-center gap-2">
              <Checkbox
                id="src-summary"
                checked={options.include_summary}
                onCheckedChange={(v) => handleSummaryChange(v === true)}
              />
              <Label htmlFor="src-summary" className="text-xs cursor-pointer">
                요약 (summary)
              </Label>
            </div>
          )}

          {hasTranscription && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                <Checkbox
                  id="src-transcription"
                  checked={options.include_transcription}
                  onCheckedChange={(v) => handleTranscriptionChange(v === true)}
                />
                <Label htmlFor="src-transcription" className="text-xs cursor-pointer">
                  전사 (transcription)
                </Label>
              </div>

              {options.include_transcription && speakers.length > 0 && (
                <div className="ml-6 space-y-1">
                  {speakers.map((speaker) => (
                    <div key={speaker} className="flex items-center gap-2">
                      <Checkbox
                        id={`src-speaker-${speaker}`}
                        checked={activeSpeakers.includes(speaker)}
                        onCheckedChange={(v) =>
                          handleSpeakerChange(speaker, v === true)
                        }
                      />
                      <Label
                        htmlFor={`src-speaker-${speaker}`}
                        className={cn(
                          'text-xs cursor-pointer',
                          !activeSpeakers.includes(speaker) && 'text-muted-foreground'
                        )}
                      >
                        {speaker}
                      </Label>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
