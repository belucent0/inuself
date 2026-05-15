/**
 * 요약 표시 컴포넌트
 * - 상태별 분기: 대기/처리중/실패/완료
 * - SUMMARIZING + summary_sections 시 block 단위 점진 렌더링
 * - COMPLETED + summary_sections 시 block 단위 카드 + hover [↻] 부분 재생성
 * - sections 없을 때만 summary_md 마크다운 렌더링
 */

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import type { ContentDetail, SummaryBlock, SummarySections } from '../types'
import { MarkdownContent } from '@/features/chat/components/MarkdownContent'
import { Button } from '@/shared/components/ui/button'
import { Badge } from '@/shared/components/ui/badge'
import { contentsApi } from '@/shared/services/endpoints/contents'
import { contentKeys } from '@/shared/hooks/useContents'
import {
  Loader2,
  AlertCircle,
  Clock,
  RotateCcw,
  CheckCircle2,
} from 'lucide-react'

interface SummaryDisplayProps {
  content: ContentDetail
  onRetryClick?: (type: 'asr' | 'ocr' | 'summary') => void
}

function countSuccessBlocks(sections: SummarySections | null | undefined): number {
  if (!sections?.blocks) return 0
  return sections.blocks.filter((b) => b.status === 'success').length
}

function findBlock(sections: SummarySections, key: string): SummaryBlock | undefined {
  return sections.blocks.find((b) => b.key === key)
}

function getSectionBlocks(sections: SummarySections): SummaryBlock[] {
  return sections.blocks
    .filter((b) => b.key.startsWith('section_'))
    .sort((a, b) => {
      const ai = Number(a.key.replace('section_', '')) || 0
      const bi = Number(b.key.replace('section_', '')) || 0
      return ai - bi
    })
}

/**
 * Hover 시 [↻] 버튼 노출 카드 — block 단위 부분 재생성 트리거.
 * COMPLETED 상태에서만 활성. SUMMARIZING 중에는 disabled.
 */
function RegenerableBlockCard({
  contentId,
  block,
  blockLabel,
  isInteractive,
  onRegenerating,
  children,
}: {
  contentId: string
  block: { key: string; status: string }
  blockLabel: string
  isInteractive: boolean
  onRegenerating: (key: string | null) => void
  children: React.ReactNode
}) {
  const [hovered, setHovered] = useState(false)
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: () => contentsApi.regenerateSummaryBlock(contentId, block.key),
    onMutate: () => {
      onRegenerating(block.key)
      toast.info(`'${blockLabel}' 재생성 중...`)
    },
    onSuccess: (data) => {
      toast.success(data.message || `'${blockLabel}' 재생성 완료`)
      queryClient.invalidateQueries({ queryKey: contentKeys.detail(contentId) })
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : '재생성 실패'
      toast.error(`'${blockLabel}' 재생성 실패: ${msg}`)
    },
    onSettled: () => {
      onRegenerating(null)
    },
  })

  const isPending = mutation.isPending

  return (
    <div
      className="relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {children}
      {isInteractive && (hovered || isPending) && (
        <Button
          variant="ghost"
          size="icon"
          className="absolute top-1 right-1 h-7 w-7 bg-background/80 backdrop-blur shadow-sm"
          onClick={() => mutation.mutate()}
          disabled={isPending}
          title={`'${blockLabel}' 재생성`}
        >
          {isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RotateCcw className="h-3.5 w-3.5" />
          )}
        </Button>
      )}
    </div>
  )
}

/**
 * block 단위 렌더링: SUMMARIZING/COMPLETED 모두 사용. 완료된 block만 표시.
 * COMPLETED + isInteractive=true 시 각 block hover [↻] → 부분 재생성.
 */
function ProgressiveSummary({
  contentId,
  sections,
  isInteractive,
}: {
  contentId: string
  sections: SummarySections
  isInteractive: boolean
}) {
  const [regeneratingKey, setRegeneratingKey] = useState<string | null>(null)
  const title = findBlock(sections, 'title')
  const keywords = findBlock(sections, 'keywords')
  const headings = findBlock(sections, 'headings')
  const sectionBlocks = getSectionBlocks(sections)

  const total = sections.blocks.length || 1
  const success = countSuccessBlocks(sections)
  const failed = sections.blocks.filter((b) => b.status === 'failed').length
  const pct = Math.round((success / total) * 100)

  const sectionLabels = headings?.status === 'success' && Array.isArray(headings.content)
    ? (headings.content as string[])
    : []

  // 진행 중 카드 (SUMMARIZING 또는 부분 재생성 중)
  const showProgressCard = !isInteractive || regeneratingKey !== null

  return (
    <div className="space-y-5">
      {showProgressCard && (
        <div className="rounded-lg border bg-card p-4">
          <div className="flex items-center gap-2 mb-2">
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
            <span className="text-sm font-medium">
              {regeneratingKey ? `'${regeneratingKey}' 재생성 중` : '요약 생성 중'}
            </span>
            <Badge variant="outline" className="text-xs ml-auto">
              {success}/{total} 완료
              {failed > 0 && <span className="text-destructive ml-1">· 재시도 {failed}</span>}
            </Badge>
          </div>
          <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-primary transition-all duration-300"
              style={{ width: `${pct}%` }}
            />
          </div>
          {!isInteractive && (
            <p className="text-xs text-muted-foreground mt-2">
              섹션이 완료되는 대로 아래에 점진적으로 표시됩니다
            </p>
          )}
        </div>
      )}

      {title?.status === 'success' && typeof title.content === 'string' && (
        <RegenerableBlockCard
          contentId={contentId}
          block={{ key: 'title', status: title.status }}
          blockLabel="제목"
          isInteractive={isInteractive}
          onRegenerating={setRegeneratingKey}
        >
          <h2 className="text-lg font-semibold pr-9">{title.content}</h2>
        </RegenerableBlockCard>
      )}

      {keywords?.status === 'success' && Array.isArray(keywords.content) && (
        <RegenerableBlockCard
          contentId={contentId}
          block={{ key: 'keywords', status: keywords.status }}
          blockLabel="키워드"
          isInteractive={isInteractive}
          onRegenerating={setRegeneratingKey}
        >
          <div className="pr-9">
            <h3 className="text-sm font-semibold mb-2">키워드</h3>
            <div className="flex flex-wrap gap-1.5">
              {(keywords.content as string[]).map((kw, i) => (
                <Badge key={i} variant="secondary" className="text-xs">
                  {kw}
                </Badge>
              ))}
            </div>
          </div>
        </RegenerableBlockCard>
      )}

      {sectionLabels.length > 0 && (
        <RegenerableBlockCard
          contentId={contentId}
          block={{ key: 'headings', status: headings?.status || 'success' }}
          blockLabel="목차"
          isInteractive={isInteractive}
          onRegenerating={setRegeneratingKey}
        >
          <div className="pr-9">
            <h3 className="text-sm font-semibold mb-2">목차</h3>
            <ul className="text-sm space-y-1 ml-1">
              {sectionLabels.map((label, i) => {
                const block = sectionBlocks.find(
                  (b) => b.key === `section_${i}` || b.label === label
                )
                const isDone = block?.status === 'success'
                const isFailed = block?.status === 'failed'
                return (
                  <li key={i} className="flex items-center gap-2">
                    {isDone ? (
                      <CheckCircle2 className="h-3.5 w-3.5 text-primary shrink-0" />
                    ) : isFailed ? (
                      <RotateCcw className="h-3.5 w-3.5 text-muted-foreground animate-pulse shrink-0" />
                    ) : (
                      <Loader2 className="h-3.5 w-3.5 text-muted-foreground animate-spin shrink-0" />
                    )}
                    <span
                      className={
                        isDone
                          ? 'text-foreground'
                          : 'text-muted-foreground'
                      }
                    >
                      {label}
                    </span>
                  </li>
                )
              })}
            </ul>
          </div>
        </RegenerableBlockCard>
      )}

      {sectionBlocks.some((b) => b.status === 'success') && (
        <div className="space-y-4">
          <h3 className="text-sm font-semibold">상세 내용</h3>
          {sectionBlocks
            .filter((b) => b.status === 'success' && typeof b.content === 'string')
            .map((b) => (
              <RegenerableBlockCard
                key={b.key}
                contentId={contentId}
                block={{ key: b.key, status: b.status }}
                blockLabel={b.label}
                isInteractive={isInteractive}
                onRegenerating={setRegeneratingKey}
              >
                <div className="rounded-md border bg-card/50 p-3 pr-10">
                  <h4 className="text-sm font-medium mb-1.5">{b.label}</h4>
                  <p className="text-sm text-muted-foreground whitespace-pre-line">
                    {b.content as string}
                  </p>
                </div>
              </RegenerableBlockCard>
            ))}
        </div>
      )}
    </div>
  )
}

function SummaryStatusCard({
  status,
  onRetryClick,
}: {
  status: string
  onRetryClick?: (type: 'asr' | 'ocr' | 'summary') => void
}) {
  if (['QUEUED', 'PULLING', 'PROCESSING', 'OCR_PROCESSING'].includes(status)) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-8 w-8 animate-spin mb-3" />
        <p className="text-sm font-medium">콘텐츠 처리 중...</p>
        <p className="text-xs mt-1">처리가 완료되면 요약이 표시됩니다</p>
      </div>
    )
  }

  if (status === 'SUMMARY_QUEUED') {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Clock className="h-8 w-8 mb-3" />
        <p className="text-sm font-medium">요약 대기 중</p>
        <p className="text-xs mt-1">곧 요약이 시작됩니다</p>
      </div>
    )
  }

  if (status === 'SUMMARIZING') {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Loader2 className="h-8 w-8 animate-spin mb-3" />
        <p className="text-sm font-medium">요약 생성 중...</p>
      </div>
    )
  }

  if (['ASR_FAILED', 'OCR_FAILED', 'SUMMARY_FAILED'].includes(status)) {
    const failType = status === 'ASR_FAILED'
      ? 'asr'
      : status === 'OCR_FAILED'
        ? 'ocr'
        : 'summary'
    const failLabel = status === 'ASR_FAILED'
      ? '음성 인식'
      : status === 'OCR_FAILED'
        ? '문서 인식'
        : '요약 생성'

    return (
      <div className="flex flex-col items-center justify-center py-12">
        <AlertCircle className="h-8 w-8 text-destructive mb-3" />
        <p className="text-sm font-medium text-destructive">{failLabel} 실패</p>
        <p className="text-xs text-muted-foreground mt-1">
          재시도하여 다시 처리할 수 있습니다
        </p>
        {onRetryClick && (
          <Button
            variant="outline"
            size="sm"
            className="mt-4 gap-1.5"
            onClick={() => onRetryClick(failType as 'asr' | 'ocr' | 'summary')}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            재시도
          </Button>
        )}
      </div>
    )
  }

  return null
}

export function SummaryDisplay({
  content,
  onRetryClick,
}: SummaryDisplayProps) {
  const summaryText = content.summary_md || content.summary_html || content.summary
  const sections = content.summary_sections

  // SUMMARIZING + 점진 렌더링 (성공 block ≥ 1)
  if (content.status === 'SUMMARIZING' && sections && countSuccessBlocks(sections) > 0) {
    return (
      <div className="max-w-3xl">
        <ProgressiveSummary
          contentId={content.id}
          sections={sections}
          isInteractive={false}
        />
      </div>
    )
  }

  if (content.status !== 'COMPLETED') {
    return (
      <SummaryStatusCard status={content.status} onRetryClick={onRetryClick} />
    )
  }

  // COMPLETED: sections가 있으면 block 단위 카드 + hover [↻] 부분 재생성
  if (sections && countSuccessBlocks(sections) > 0) {
    return (
      <div className="max-w-3xl">
        <ProgressiveSummary
          contentId={content.id}
          sections={sections}
          isInteractive={true}
        />
      </div>
    )
  }

  // sections 없으면 fallback: 기존 markdown 렌더링
  return (
    <div className="space-y-6 max-w-3xl">
      {summaryText ? (
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <MarkdownContent content={summaryText} />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">요약 내용이 없습니다.</p>
      )}
    </div>
  )
}
