/**
 * 요약 표시 컴포넌트
 * - 상태별 분기: 대기/처리중/실패/완료
 * - SUMMARIZING + summary_sections 시 block 단위 점진 렌더링
 * - COMPLETED 시 summary_md를 MarkdownContent로 렌더링
 */

import type { ContentDetail, SummaryBlock, SummarySections } from '../types'
import { MarkdownContent } from '@/features/chat/components/MarkdownContent'
import { Button } from '@/shared/components/ui/button'
import { Badge } from '@/shared/components/ui/badge'
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
 * 점진 렌더링: SUMMARIZING 중이라도 완료된 block만 골라 부분 표시.
 * keywords/headings/section_* 순으로 toc 순서 보존.
 */
function ProgressiveSummary({ sections }: { sections: SummarySections }) {
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

  return (
    <div className="space-y-5">
      <div className="rounded-lg border bg-card p-4">
        <div className="flex items-center gap-2 mb-2">
          <Loader2 className="h-4 w-4 animate-spin text-primary" />
          <span className="text-sm font-medium">요약 생성 중</span>
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
        <p className="text-xs text-muted-foreground mt-2">
          섹션이 완료되는 대로 아래에 점진적으로 표시됩니다
        </p>
      </div>

      {title?.status === 'success' && typeof title.content === 'string' && (
        <div>
          <h2 className="text-lg font-semibold">{title.content}</h2>
        </div>
      )}

      {keywords?.status === 'success' && Array.isArray(keywords.content) && (
        <div>
          <h3 className="text-sm font-semibold mb-2">키워드</h3>
          <div className="flex flex-wrap gap-1.5">
            {(keywords.content as string[]).map((kw, i) => (
              <Badge key={i} variant="secondary" className="text-xs">
                {kw}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {sectionLabels.length > 0 && (
        <div>
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
      )}

      {sectionBlocks.some((b) => b.status === 'success') && (
        <div className="space-y-4">
          <h3 className="text-sm font-semibold">상세 내용</h3>
          {sectionBlocks
            .filter((b) => b.status === 'success' && typeof b.content === 'string')
            .map((b) => (
              <div key={b.key} className="rounded-md border bg-card/50 p-3">
                <h4 className="text-sm font-medium mb-1.5">{b.label}</h4>
                <p className="text-sm text-muted-foreground whitespace-pre-line">
                  {b.content as string}
                </p>
              </div>
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
        <ProgressiveSummary sections={sections} />
      </div>
    )
  }

  if (content.status !== 'COMPLETED') {
    return (
      <SummaryStatusCard status={content.status} onRetryClick={onRetryClick} />
    )
  }

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
