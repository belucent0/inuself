/**
 * 콘텐츠 상세 레이아웃 오케스트레이터
 * - 데스크톱: 3단 컬럼 (미디어+데이터 | 요약 | 채팅)
 * - 모바일: 풀스크린 3탭 (요약 | 소스 | 채팅)
 */

import { useEffect, useRef, useState } from 'react'
import type { ContentDetail } from '../types'
import { ContentHeader } from './ContentHeader'
import { SummaryDisplay } from './SummaryDisplay'
import { ContentChatPanel } from './ContentChatPanel'
import { MediaViewer } from './viewers/MediaViewer'
import { TranscriptionSegments } from './results/TranscriptionSegments'
import { OcrTextDisplay } from './results/OcrTextDisplay'
import { OcrRetryModal } from './modals/OcrRetryModal'
import { AsrRetryModal } from './modals/AsrRetryModal'
import type { OcrMode, AccuracyMode } from './modals/OcrRetryModal'
import type { AsrRetryOptions } from './modals/AsrRetryModal'
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/shared/components/ui/tabs'
import { useIsMobile } from '@/shared/hooks/use-mobile'
import { ClipboardList, MessageSquare, Eye } from 'lucide-react'
import { cn } from '@/shared/utils/cn'
import { toast } from 'sonner'

interface ContentDetailLayoutProps {
  content: ContentDetail
  onDelete: () => Promise<void>
  onRetry: (
    type: 'asr' | 'ocr' | 'summary',
    options?: {
      minSpeakers?: number
      maxSpeakers?: number
      ocrMode?: string
      accuracyMode?: string
    }
  ) => Promise<void>
  refetch: () => void
}

export function ContentDetailLayout({
  content,
  onDelete,
  onRetry,
  refetch,
}: ContentDetailLayoutProps) {
  const mediaRef = useRef<HTMLMediaElement | null>(null)
  const [activeTab, setActiveTab] = useState('summary')
  const isMobile = useIsMobile()

  const [ocrRetryOpen, setOcrRetryOpen] = useState(false)
  const [asrRetryOpen, setAsrRetryOpen] = useState(false)
  const [isRetrying, setIsRetrying] = useState(false)

  // 재처리 후 backend background task가 status 전이 반영할 시간 확보 (SSE 미연결 환경 fallback).
  // 즉시 refetch는 backend status 변경 전이라 의미 없어 생략.
  const refetchTimersRef = useRef<number[]>([])
  useEffect(
    () => () => {
      refetchTimersRef.current.forEach(clearTimeout)
      refetchTimersRef.current = []
    },
    []
  )
  const refetchWithDelay = () => {
    refetchTimersRef.current.push(window.setTimeout(refetch, 300))
    refetchTimersRef.current.push(window.setTimeout(refetch, 2000))
  }

  const handleRetryClick = async (type: 'asr' | 'ocr' | 'summary') => {
    if (type === 'asr') {
      setAsrRetryOpen(true)
    } else if (type === 'ocr') {
      setOcrRetryOpen(true)
    } else {
      if (confirm('LLM 요약을 다시 시도하시겠습니까?')) {
        setIsRetrying(true)
        try {
          await onRetry('summary')
          toast.success('요약 재처리가 시작되었습니다')
          refetchWithDelay()
        } catch {
          toast.error('요약 재처리 요청에 실패했습니다')
        } finally {
          setIsRetrying(false)
        }
      }
    }
  }

  const handleOcrRetry = async (ocrMode: OcrMode, accuracyMode: AccuracyMode) => {
    setIsRetrying(true)
    try {
      await onRetry('ocr', { ocrMode, accuracyMode })
      setOcrRetryOpen(false)
      refetchWithDelay()
    } finally {
      setIsRetrying(false)
    }
  }

  const handleAsrRetry = async (options: AsrRetryOptions) => {
    setIsRetrying(true)
    try {
      await onRetry('asr', {
        accuracyMode: options.accuracyMode,
        minSpeakers: options.minSpeakers,
        maxSpeakers: options.maxSpeakers,
      })
      setAsrRetryOpen(false)
      refetchWithDelay()
    } finally {
      setIsRetrying(false)
    }
  }

  const retryModals = (
    <>
      <OcrRetryModal
        open={ocrRetryOpen}
        onOpenChange={setOcrRetryOpen}
        filename={content.filename}
        onConfirm={handleOcrRetry}
        isLoading={isRetrying}
      />
      <AsrRetryModal
        open={asrRetryOpen}
        onOpenChange={setAsrRetryOpen}
        onConfirm={handleAsrRetry}
        isLoading={isRetrying}
      />
    </>
  )

  const hasTranscription =
    content.content_type === 'AUDIO' &&
    content.transcription?.segments &&
    content.transcription.segments.length > 0

  const hasDocument =
    (content.content_type === 'DOCUMENT' || content.content_type === 'PORTRAY') &&
    content.document &&
    (content.document.text_content ||
      content.document.ocr_text ||
      content.document.html_content)

  // 모바일: 3탭 풀스크린
  if (isMobile) {
    return (
      <div className="flex flex-col h-full">
        <ContentHeader
          content={content}
          onDelete={onDelete}
          onRetryClick={handleRetryClick}
        />
        <Tabs
          value={activeTab}
          onValueChange={setActiveTab}
          className="flex-1 flex flex-col min-h-0"
        >
          <TabsList className="w-full rounded-none border-b bg-background h-10 shrink-0">
            <TabsTrigger value="summary" className="flex-1 gap-1.5">
              <ClipboardList className="h-3.5 w-3.5" />
              요약
            </TabsTrigger>
            <TabsTrigger value="source" className="flex-1 gap-1.5">
              <Eye className="h-3.5 w-3.5" />
              소스
            </TabsTrigger>
            <TabsTrigger value="chat" className="flex-1 gap-1.5">
              <MessageSquare className="h-3.5 w-3.5" />
              채팅
            </TabsTrigger>
          </TabsList>
          <TabsContent
            value="summary"
            className="flex-1 overflow-y-auto mt-0 p-4"
          >
            <SummaryDisplay
              content={content}
              onRetryClick={handleRetryClick}
            />
          </TabsContent>
          <TabsContent
            value="source"
            className="flex-1 overflow-y-auto mt-0"
          >
            <div className="p-3 space-y-3">
              <MediaViewer content={content} mediaRef={mediaRef} />
              {hasTranscription && (
                <TranscriptionSegments
                  segments={content.transcription!.segments}
                  speakers={content.transcription!.speakers}
                  mediaRef={mediaRef}
                  contentId={content.id}
                />
              )}
              {hasDocument && content.document && (
                <OcrTextDisplay document={content.document} />
              )}
            </div>
          </TabsContent>
          <TabsContent
            value="chat"
            forceMount
            className={cn(
              'flex-1 mt-0 overflow-hidden',
              activeTab !== 'chat' && 'hidden'
            )}
          >
            <ContentChatPanel
              content={content}
              contentTitle={content.title || content.filename}
            />
          </TabsContent>
        </Tabs>
        {retryModals}
      </div>
    )
  }

  // 데스크톱: 3단 컬럼
  return (
    <div className="flex flex-col h-full">
      <ContentHeader
        content={content}
        onDelete={onDelete}
        onRetryClick={handleRetryClick}
      />
      <div className="flex flex-1 min-h-0">
        {/* 좌: 미디어 + 처리데이터 */}
        <div className="flex-1 min-w-0 border-r flex flex-col">
          <div className="shrink-0 p-3 border-b">
            <MediaViewer content={content} mediaRef={mediaRef} />
          </div>
          <div className="flex-1 min-h-0 flex flex-col">
            {hasTranscription && (
              <TranscriptionSegments
                segments={content.transcription!.segments}
                speakers={content.transcription!.speakers}
                mediaRef={mediaRef}
                contentId={content.id}
              />
            )}
            {hasDocument && content.document && (
              <OcrTextDisplay document={content.document} />
            )}
          </div>
        </div>

        {/* 중: 요약 */}
        <div className="flex-1 min-w-0 overflow-y-auto p-6 border-r">
          <SummaryDisplay
            content={content}
            onRetryClick={handleRetryClick}
          />
        </div>

        {/* 우: 채팅 */}
        <div className="flex-1 min-w-0">
          <ContentChatPanel
            content={content}
            contentTitle={content.title || content.filename}
          />
        </div>
      </div>
      {retryModals}
    </div>
  )
}
