/**
 * 소스 컨텍스트 팝오버
 * SourceOptionsPanel + ContentSearchPopover를 통합한 단일 팝오버
 * - 검색 범위 (선택 문서 / 전체 내부 문서 / 웹 검색)
 * - 소스 설정 (요약 / 전사 / 화자 필터)
 * - 인라인 문서 검색 (cmdk 없이 일반 input + 리스트)
 */

import { useState, useCallback } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Globe,
  Database,
  FileText,
  X,
  Search,
  ArrowLeft,
  BookOpen,
} from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Checkbox } from '@/shared/components/ui/checkbox'
import { Label } from '@/shared/components/ui/label'
import { Badge } from '@/shared/components/ui/badge'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/shared/components/ui/popover'
import { cn } from '@/shared/utils/cn'
import { useContentSearch } from '@/shared/hooks/useContentSearch'
import type { ContentDetail, ContentSummary } from '../types'

export interface SourceOptions {
  include_summary: boolean
  include_transcription: boolean
  speaker_filter: string[] | null
  include_web_search: boolean
  selected_content_ids: string[]
  include_all_docs: boolean
}

interface SourceContextPopoverProps {
  content: ContentDetail
  options: SourceOptions
  onChange: (options: SourceOptions) => void
  fixedContentId: string
  additionalContents?: ContentSummary[]
  onAddContent: (content: ContentSummary) => void
  onRemoveContent: (id: string) => void
  disabled?: boolean
}

/** 현재 소스 상태에 따라 버튼 라벨 결정 */
function getButtonLabel(options: SourceOptions, additionalCount: number): string {
  if (options.include_all_docs && options.include_web_search) return '전체 + 웹'
  if (options.include_web_search) return '문서 + 웹'
  if (options.include_all_docs) return '전체 문서'
  if (additionalCount > 0) return `문서 ${additionalCount + 1}개`
  return '이 문서'
}

/** 버튼 이모지 아이콘 결정 */
function getButtonIcon(options: SourceOptions): React.ReactNode {
  if (options.include_web_search) return <Globe className="h-3.5 w-3.5" />
  if (options.include_all_docs) return <BookOpen className="h-3.5 w-3.5" />
  return <FileText className="h-3.5 w-3.5" />
}

/** 문서 검색 뷰 (팝오버 내부) */
function SearchView({
  selectedIds,
  fixedId,
  onSelect,
  onDeselect,
  onBack,
}: {
  selectedIds: string[]
  fixedId: string
  onSelect: (c: ContentSummary) => void
  onDeselect: (id: string) => void
  onBack: () => void
}) {
  const [query, setQuery] = useState('')
  const { contents, isLoading } = useContentSearch(query)

  return (
    <div className="flex flex-col gap-1">
      {/* 검색 입력 */}
      <div className="relative px-3 pt-2">
        <Search className="absolute left-5 top-1/2 -translate-y-1/2 mt-1 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
        <input
          autoFocus
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="콘텐츠 검색..."
          className="w-full pl-7 pr-3 py-1.5 text-xs bg-muted/50 border border-input rounded-md outline-none focus:ring-1 focus:ring-ring"
        />
      </div>

      {/* 구분선 */}
      <div className="border-t border-border/50 mx-1 mt-1" />

      {/* 검색 결과 리스트 */}
      <div className="max-h-52 overflow-y-auto px-1 pb-1">
        {isLoading ? (
          <div className="py-4 text-center text-xs text-muted-foreground">검색 중...</div>
        ) : contents.length === 0 ? (
          <div className="py-4 text-center text-xs text-muted-foreground">콘텐츠를 찾을 수 없습니다.</div>
        ) : (
          contents.map((c) => {
            const isFixed = c.id === fixedId
            const isSelected = selectedIds.includes(c.id)
            return (
              <button
                key={c.id}
                type="button"
                disabled={isFixed}
                onClick={() => {
                  if (isFixed) return
                  if (isSelected) {
                    onDeselect(c.id)
                  } else {
                    onSelect(c)
                  }
                }}
                className={cn(
                  'flex items-center gap-2 w-full px-2 py-1.5 rounded-md text-left transition-colors',
                  isFixed
                    ? 'opacity-50 cursor-default'
                    : 'hover:bg-muted/70 cursor-pointer'
                )}
              >
                <Checkbox
                  checked={isSelected}
                  className="shrink-0 pointer-events-none"
                  tabIndex={-1}
                />
                <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate text-xs flex-1">
                  {c.title || c.filename}
                </span>
                {isFixed && (
                  <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 shrink-0">
                    고정
                  </Badge>
                )}
              </button>
            )
          })
        )}
      </div>

      {/* 구분선 */}
      <div className="border-t border-border/50 mx-1" />

      {/* 돌아가기 */}
      <div className="px-2 py-1.5">
        <button
          type="button"
          onClick={onBack}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          돌아가기
        </button>
      </div>
    </div>
  )
}

export function SourceContextPopover({
  content,
  options,
  onChange,
  fixedContentId,
  additionalContents = [],
  onAddContent,
  onRemoveContent,
  disabled,
}: SourceContextPopoverProps) {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<'main' | 'search'>('main')
  const [sourceExpanded, setSourceExpanded] = useState(false)

  const hasSummary = !!content.summary_md
  const speakers: string[] = content.transcription?.speakers ?? []
  const hasTranscription =
    speakers.length > 0 ||
    (content.content_type === 'AUDIO' && !!content.transcription?.segments?.length)

  const handleSummaryChange = (checked: boolean) => {
    onChange({ ...options, include_summary: checked })
  }

  const handleTranscriptionChange = (checked: boolean) => {
    onChange({
      ...options,
      include_transcription: checked,
      speaker_filter: checked ? options.speaker_filter : null,
    })
  }

  const handleSpeakerChange = (speaker: string, checked: boolean) => {
    const current = options.speaker_filter ?? speakers
    const next = checked
      ? [...current, speaker]
      : current.filter((s) => s !== speaker)
    onChange({
      ...options,
      speaker_filter: next.length === speakers.length ? null : next,
    })
  }

  const activeSpeakers = options.speaker_filter ?? speakers

  const handleOpenChange = useCallback(
    (isOpen: boolean) => {
      setOpen(isOpen)
      if (!isOpen) {
        // 팝오버 닫힐 때 검색 뷰 초기화
        setTimeout(() => setView('main'), 200)
      }
    },
    []
  )

  const buttonLabel = getButtonLabel(options, additionalContents.length)
  const buttonIcon = getButtonIcon(options)

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled}
          className="h-7 px-3 rounded-full text-xs font-medium gap-1.5 text-muted-foreground hover:text-foreground hover:bg-muted/50"
        >
          {buttonIcon}
          {buttonLabel}
          <ChevronDown className="h-3 w-3 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-72 p-0 overflow-hidden"
        align="start"
        side="top"
        sideOffset={8}
      >
        {view === 'search' ? (
          <SearchView
            selectedIds={options.selected_content_ids}
            fixedId={fixedContentId}
            onSelect={onAddContent}
            onDeselect={onRemoveContent}
            onBack={() => setView('main')}
          />
        ) : (
          <div className="flex flex-col gap-0">
            {/* 검색 범위 섹션 */}
            <div className="px-3 pt-3 pb-2 space-y-2">
              {/* 선택한 문서 헤더 */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-medium text-foreground">
                  <FileText className="h-3.5 w-3.5 text-blue-500" />
                  선택한 문서
                </div>
                <button
                  type="button"
                  onClick={() => setView('search')}
                  className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  <Search className="h-3 w-3" />
                  + 추가
                </button>
              </div>

              {/* 고정 콘텐츠 */}
              <div className="flex items-center gap-1.5 px-1">
                <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
                <span className="text-xs truncate flex-1 text-foreground">
                  {content.title || content.filename}
                </span>
                <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 shrink-0">
                  고정
                </Badge>
              </div>

              {/* 추가된 문서들 */}
              {additionalContents.map((c) => (
                <div key={c.id} className="flex items-center gap-1.5 px-1">
                  <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
                  <span className="text-xs truncate flex-1 text-muted-foreground">
                    {c.title || c.filename}
                  </span>
                  <button
                    type="button"
                    onClick={() => onRemoveContent(c.id)}
                    className="shrink-0 text-muted-foreground hover:text-foreground transition-colors"
                    aria-label="문서 제거"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}

              {/* 전체 내부 문서 체크박스 */}
              <div className="flex items-center gap-2 pt-1">
                <Checkbox
                  id="ctx-all-docs"
                  checked={options.include_all_docs}
                  onCheckedChange={(v) =>
                    onChange({ ...options, include_all_docs: v === true })
                  }
                />
                <Label
                  htmlFor="ctx-all-docs"
                  className="flex items-center gap-1.5 text-xs cursor-pointer"
                >
                  <Database className="h-3 w-3 text-purple-500" />
                  전체 내부 문서 검색
                </Label>
              </div>

              {/* 웹 검색 체크박스 */}
              <div className="flex items-center gap-2">
                <Checkbox
                  id="ctx-web-search"
                  checked={options.include_web_search}
                  onCheckedChange={(v) =>
                    onChange({ ...options, include_web_search: v === true })
                  }
                />
                <Label
                  htmlFor="ctx-web-search"
                  className="flex items-center gap-1.5 text-xs cursor-pointer"
                >
                  <Globe className="h-3 w-3 text-blue-500" />
                  웹 검색 포함
                </Label>
              </div>
            </div>

            {/* 소스 설정 - 접이식 (요약/전사/화자) */}
            {(hasSummary || hasTranscription) && (
              <>
                <div className="border-t border-border/50" />
                <button
                  type="button"
                  onClick={() => setSourceExpanded((v) => !v)}
                  className="flex items-center gap-1.5 w-full px-3 py-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
                >
                  {sourceExpanded ? (
                    <ChevronDown className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                  )}
                  소스 설정
                </button>

                {sourceExpanded && (
                  <div className="px-4 pb-3 space-y-2">
                    {hasSummary && (
                      <div className="flex items-center gap-2">
                        <Checkbox
                          id="ctx-summary"
                          checked={options.include_summary}
                          onCheckedChange={(v) => handleSummaryChange(v === true)}
                        />
                        <Label htmlFor="ctx-summary" className="text-xs cursor-pointer">
                          요약 (summary)
                        </Label>
                      </div>
                    )}

                    {hasTranscription && (
                      <div className="space-y-1.5">
                        <div className="flex items-center gap-2">
                          <Checkbox
                            id="ctx-transcription"
                            checked={options.include_transcription}
                            onCheckedChange={(v) => handleTranscriptionChange(v === true)}
                          />
                          <Label htmlFor="ctx-transcription" className="text-xs cursor-pointer">
                            전사 (transcription)
                          </Label>
                        </div>

                        {options.include_transcription && speakers.length > 0 && (
                          <div className="ml-6 space-y-1">
                            {speakers.map((speaker) => (
                              <div key={speaker} className="flex items-center gap-2">
                                <Checkbox
                                  id={`ctx-speaker-${speaker}`}
                                  checked={activeSpeakers.includes(speaker)}
                                  onCheckedChange={(v) =>
                                    handleSpeakerChange(speaker, v === true)
                                  }
                                />
                                <Label
                                  htmlFor={`ctx-speaker-${speaker}`}
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
              </>
            )}
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}
