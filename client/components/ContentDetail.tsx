'use client'

import { useState, useRef, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowUp, Download, FileText, Music, Trash2 } from 'lucide-react'

import { ContentDetail as ContentDetailType, retryProcessing, reclusterSpeakers, deleteContentsBulk } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatToKST } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import DocumentViewer from '@/components/DocumentViewer'
import MarkdownContent from '@/components/MarkdownContent'
import HtmlContent from '@/components/HtmlContent'

type Props = {
  content: ContentDetailType
}

// 오디오 파일인지 확인하는 헬퍼 함수
function isAudioFile(filename: string): boolean {
  const audioExtensions = ['.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.wma']
  const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
  return audioExtensions.includes(ext)
}

// 비디오 파일인지 확인하는 헬퍼 함수
function isVideoFile(filename: string): boolean {
  const videoExtensions = ['.mp4', '.avi', '.mkv', '.mov', '.webm']
  const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
  return videoExtensions.includes(ext)
}

// 문서 파일인지 확인하는 헬퍼 함수
function isDocumentFile(filename: string): boolean {
  const documentExtensions = ['.txt', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']
  const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
  return documentExtensions.includes(ext)
}

// 파일 확장자 가져오기
function getFileExtension(filename: string): string {
  return filename.toLowerCase().substring(filename.lastIndexOf('.'))
}

// 이미지 파일인지 확인
function isImageFile(filename: string): boolean {
  const imageExtensions = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp']
  return imageExtensions.includes(getFileExtension(filename))
}

// PDF 파일인지 확인
function isPdfFile(filename: string): boolean {
  return getFileExtension(filename) === '.pdf'
}

// DOCX 파일인지 확인 (미리보기 지원: .docx만)
function isDocxFile(filename: string): boolean {
  const ext = getFileExtension(filename)
  return ext === '.docx'  // .doc는 미리보기 미지원
}

// TXT 파일인지 확인
function isTxtFile(filename: string): boolean {
  return getFileExtension(filename) === '.txt'
}

// Office 파일인지 확인 (미리보기 미지원: .doc, .xls, .xlsx, .ppt, .pptx)
function isOfficeFile(filename: string): boolean {
  const ext = getFileExtension(filename)
  return ['.doc', '.xls', '.xlsx', '.ppt', '.pptx'].includes(ext)
}

export default function ContentDetail({ content }: Props) {
  const router = useRouter()
  const [message, setMessage] = useState<string>('')
  const [reclusterMessage, setReclusterMessage] = useState<string>('')
  const [isReclustering, setIsReclustering] = useState<boolean>(false)
  const [numSpeakers, setNumSpeakers] = useState<string>('')
  const [similarityThreshold, setSimilarityThreshold] = useState<number>(0.7)
  const [minSpeakers, setMinSpeakers] = useState<string>('')
  const [maxSpeakers, setMaxSpeakers] = useState<string>('')
  const [currentSegmentId, setCurrentSegmentId] = useState<number | null>(null)
  const [autoScroll, setAutoScroll] = useState<boolean>(false)
  const [showScrollTop, setShowScrollTop] = useState<boolean>(false)
  const [showOcrRetryModal, setShowOcrRetryModal] = useState(false)
  const [ocrRetryMode, setOcrRetryMode] = useState<'portray' | 'document' | null>(null)

  const previousSegmentIdRef = useRef<number | null>(null)
  const audioRef = useRef<HTMLAudioElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const segmentContainerRef = useRef<HTMLDivElement>(null)
  const segmentViewportRef = useRef<HTMLDivElement | null>(null)

  const handleRetry = async (type: 'asr' | 'summary' | 'ocr') => {
    const isDocument = isDocumentFile(content.filename)

    // OCR 재처리인 경우 모달 표시
    if (type === 'ocr') {
      setOcrRetryMode(null) // 초기화
      setShowOcrRetryModal(true)
      return
    }

    const typeLabel = type === 'asr' ? (isDocument ? 'OCR 처리' : 'ASR 처리') : 'LLM 요약'
    if (!confirm(`${typeLabel}를 다시 시도하시겠습니까?`)) {
      return
    }

    try {
      let minSpeakersValue: number | undefined = undefined
      let maxSpeakersValue: number | undefined = undefined

      // 문서 타입이 아닐 때만 화자 수 처리
      if (type === 'asr' && !isDocument) {
        if (minSpeakers.trim()) {
          const parsed = parseInt(minSpeakers.trim())
          if (isNaN(parsed) || parsed < 1) {
            throw new Error('최소 화자 수는 1 이상의 정수여야 합니다.')
          }
          minSpeakersValue = parsed
        }
        if (maxSpeakers.trim()) {
          const parsed = parseInt(maxSpeakers.trim())
          if (isNaN(parsed) || parsed < 1) {
            throw new Error('최대 화자 수는 1 이상의 정수여야 합니다.')
          }
          maxSpeakersValue = parsed
        }
        if (minSpeakersValue !== undefined && maxSpeakersValue !== undefined && minSpeakersValue > maxSpeakersValue) {
          throw new Error('최소 화자 수는 최대 화자 수보다 작거나 같아야 합니다.')
        }
      }

      const result = await retryProcessing(content.id, type, minSpeakersValue, maxSpeakersValue)
      setMessage(result.message)
      router.refresh()
      setTimeout(() => setMessage(''), 3000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '재처리 실패')
    }
  }

  const handleDelete = async () => {
    if (!confirm(`"${content.filename}"을(를) 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`)) {
      return
    }

    try {
      await deleteContentsBulk([content.id])
      setMessage('삭제되었습니다. 목록 페이지로 이동합니다...')
      setTimeout(() => {
        router.push('/contents')
      }, 1000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '삭제 실패')
    }
  }

  const handleRecluster = async () => {
    if (!content.transcription?.diarization_metadata?.segment_embeddings ||
      content.transcription.diarization_metadata.segment_embeddings.length === 0) {
      setReclusterMessage('세그먼트 임베딩이 없습니다. 먼저 화자 분리를 완료해주세요.')
      return
    }

    if (!confirm('화자 재분류를 실행하시겠습니까? 기존 화자 라벨이 변경될 수 있습니다.')) {
      return
    }

    setIsReclustering(true)
    setReclusterMessage('')

    try {
      const numSpeakersValue = numSpeakers.trim() ? parseInt(numSpeakers.trim()) : undefined
      if (numSpeakersValue !== undefined && (numSpeakersValue < 1 || isNaN(numSpeakersValue))) {
        throw new Error('화자 수는 1 이상의 정수여야 합니다.')
      }

      const result = await reclusterSpeakers(
        content.id,
        numSpeakersValue,
        similarityThreshold
      )

      setReclusterMessage(`✅ ${result.message} (${result.num_speakers}명: ${result.speaker_labels.join(', ')})`)
      router.refresh()
      setTimeout(() => setReclusterMessage(''), 5000)
    } catch (error) {
      setReclusterMessage(`❌ ${error instanceof Error ? error.message : '재클러스터링 실패'}`)
    } finally {
      setIsReclustering(false)
    }
  }

  const handleSeekToTime = (startTime: number) => {
    if (!content.media_url) {
      return
    }
    let mediaElement: HTMLMediaElement | null = null

    if (isVideoFile(content.filename)) {
      mediaElement = videoRef.current
    } else if (isAudioFile(content.filename)) {
      mediaElement = audioRef.current
    }

    if (mediaElement) {
      mediaElement.currentTime = startTime
      mediaElement.play().catch((error) => {
        console.error('재생 실패:', error)
      })
    }
  }

  const handleScrollToTop = () => {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const handleDownload = async () => {
    if (!content.media_url) {
      return
    }

    try {
      const response = await fetch(content.media_url)
      if (!response.ok) {
        throw new Error('파일 다운로드 실패')
      }

      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = content.filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
    } catch (error) {
      console.error('다운로드 오류:', error)
      alert('파일 다운로드에 실패했습니다.')
    }
  }

  const handleOcrRetryConfirm = async () => {
    if (!ocrRetryMode) return

    try {
      // 기존 handleRetry 로직과 유사하지만 ocrMode를 추가로 전달
      const result = await retryProcessing(content.id, 'ocr', undefined, undefined, ocrRetryMode)
      setMessage(result.message)
      router.refresh()
      setTimeout(() => setMessage(''), 3000)
      setShowOcrRetryModal(false)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '재처리 실패')
    }
  }

  // 스크롤 위치 추적
  useEffect(() => {
    const handleScroll = () => {
      setShowScrollTop(window.scrollY > 300)
    }

    window.addEventListener('scroll', handleScroll)
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  // 미디어 재생 시간 추적 및 세그먼트 하이라이트
  useEffect(() => {
    if (!content.media_url || !content.transcription?.segments) {
      return
    }

    const mediaElement = isAudioFile(content.filename) ? audioRef.current : videoRef.current
    if (!mediaElement) {
      return
    }

    const handleTimeUpdate = () => {
      const currentTime = mediaElement.currentTime
      const activeSegment = content.transcription?.segments?.find(
        (seg) => currentTime >= seg.start && currentTime <= seg.end
      )
      const newSegmentId = activeSegment?.id ?? null

      // 세그먼트가 실제로 변경되었을 때만 업데이트 및 스크롤
      if (newSegmentId !== previousSegmentIdRef.current) {
        setCurrentSegmentId(newSegmentId)
        previousSegmentIdRef.current = newSegmentId

        // 자동 스크롤이 활성화되어 있고 세그먼트가 변경되었을 때 스크롤
        if (autoScroll && newSegmentId !== null) {
          const segmentElement = document.getElementById(`segment-${newSegmentId}`)
          const viewport = segmentViewportRef.current
          if (segmentElement && viewport) {
            const containerRect = viewport.getBoundingClientRect()
            const elementRect = segmentElement.getBoundingClientRect()
            const scrollTop = viewport.scrollTop
            const elementOffsetTop = elementRect.top - containerRect.top + scrollTop
            const containerCenter = viewport.clientHeight / 2
            const targetScrollTop = elementOffsetTop - containerCenter + (elementRect.height / 2)

            viewport.scrollTo({
              top: targetScrollTop,
              behavior: 'smooth'
            })
          }
        }
      }
    }

    mediaElement.addEventListener('timeupdate', handleTimeUpdate)

    // 재생이 멈췄을 때 하이라이트 제거
    const handlePause = () => {
      setCurrentSegmentId(null)
      previousSegmentIdRef.current = null
    }

    mediaElement.addEventListener('pause', handlePause)
    mediaElement.addEventListener('ended', handlePause)

    return () => {
      mediaElement.removeEventListener('timeupdate', handleTimeUpdate)
      mediaElement.removeEventListener('pause', handlePause)
      mediaElement.removeEventListener('ended', handlePause)
    }
  }, [content.media_url, content.filename, content.transcription?.segments, autoScroll])

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2 min-w-0 flex-1">
              {content.content_type === 'DOCUMENT' ? (
                <FileText className="h-5 w-5 text-muted-foreground flex-shrink-0" />
              ) : content.content_type === 'AUDIO' ? (
                <Music className="h-5 w-5 text-muted-foreground flex-shrink-0" />
              ) : null}
              <CardTitle className="text-xl break-words">{content.title || content.filename}</CardTitle>
            </div>
            <Button
              type="button"
              onClick={handleDelete}
              variant="destructive"
              size="sm"
              className="flex-shrink-0"
            >
              <Trash2 className="mr-2 h-4 w-4" />
              삭제
            </Button>
          </div>
          <CardDescription className="mt-2">
            {isDocumentFile(content.filename)
              ? `문서 파일 · ${content.document ? `페이지 수: ${content.document.page_count}페이지` : '처리 중'}`
              : `총 재생 길이 ${content.duration_seconds.toFixed(1)}초 · 화자 ${content.speakers.join(', ') || '분석 중'}`
            }
          </CardDescription>
          <p className="text-xs text-muted-foreground break-all mt-2">저장 키: {content.object_key}</p>
        </CardHeader>

        {content.media_url && (
          <CardContent className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold mb-2">
                {isDocumentFile(content.filename) ? '문서 뷰어' : '미디어 재생'}
              </h3>
              {isDocumentFile(content.filename) ? (
                // 문서 뷰어
                <div className="w-full border rounded-lg overflow-hidden bg-muted/50">
                  {isTxtFile(content.filename) ? (
                    // txt 파일: 문서 뷰어는 생략하고 아래 "문서 내용" 섹션에서 표시
                    <div className="p-8 text-center">
                      <p className="text-muted-foreground">
                        텍스트 파일은 아래 "문서 내용" 섹션에서 확인할 수 있습니다.
                      </p>
                    </div>
                  ) : isPdfFile(content.filename) || isDocxFile(content.filename) ? (
                    <DocumentViewer
                      fileUrl={content.media_url}
                      filename={content.filename}
                      isPdf={isPdfFile(content.filename)}
                      isDocx={isDocxFile(content.filename)}
                    />
                  ) : isImageFile(content.filename) ? (
                    <div className="flex justify-center items-center p-4">
                      <img
                        src={content.media_url}
                        alt={content.filename}
                        className="max-w-full max-h-[800px] object-contain"
                      />
                    </div>
                  ) : isOfficeFile(content.filename) ? (
                    <div className="p-8 text-center">
                      <p className="text-muted-foreground mb-2">
                        이 파일 형식({getFileExtension(content.filename)})은 현재 미리보기를 지원하지 않습니다.
                      </p>
                      <p className="text-sm text-muted-foreground mb-4">
                        .doc, .xls, .xlsx, .ppt, .pptx 파일은 향후 지원 예정입니다.
                      </p>
                    </div>
                  ) : (
                    <div className="p-8 text-center">
                      <p className="text-muted-foreground mb-4">
                        이 파일 형식은 브라우저에서 직접 미리보기를 지원하지 않습니다.
                      </p>
                    </div>
                  )}
                </div>
              ) : isVideoFile(content.filename) ? (
                <video
                  ref={videoRef}
                  controls
                  src={content.media_url}
                  className="w-full max-h-[500px]"
                  preload="metadata"
                >
                  브라우저가 비디오 재생을 지원하지 않습니다.
                </video>
              ) : isAudioFile(content.filename) ? (
                <audio
                  ref={audioRef}
                  controls
                  src={content.media_url}
                  className="w-full max-w-2xl"
                  preload="metadata"
                >
                  브라우저가 오디오 재생을 지원하지 않습니다.
                </audio>
              ) : (
                <div className="p-8 text-center">
                  <p className="text-muted-foreground mb-4">
                    이 파일 형식은 브라우저에서 직접 미리보기를 지원하지 않습니다.
                  </p>
                  <Button
                    type="button"
                    onClick={handleDownload}
                    variant="default"
                  >
                    <Download className="mr-2 h-4 w-4" />
                    파일 다운로드
                  </Button>
                </div>
              )}
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              {!isDocumentFile(content.filename) && (
                <div className="flex items-center gap-2">
                  <Switch
                    checked={autoScroll}
                    onCheckedChange={setAutoScroll}
                    className="touch-manipulation"
                    aria-label={autoScroll ? '자동 스크롤 활성화' : '자동 스크롤 비활성화'}
                  />
                  <Label className="text-sm">스크립트 자동 스크롤</Label>
                </div>
              )}
              <Button
                type="button"
                onClick={handleDownload}
                variant="default"
                className="ml-auto"
              >
                <Download className="mr-2 h-4 w-4" />
                파일 다운로드
              </Button>
            </div>
          </CardContent>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>LLM 요약</CardTitle>
        </CardHeader>
        <CardContent>
          {content.status === 'SUMMARY_QUEUED' && (
            <div className="space-y-4">
              <p className="text-muted-foreground">요약 작업이 큐에 등록되었습니다. 잠시만 기다려 주세요.</p>
              <p className="text-xs text-muted-foreground">작업이 오래 걸린다면 아래 버튼을 눌러 재시도할 수 있습니다.</p>
              <Button
                type="button"
                onClick={() => handleRetry('summary')}
                variant="outline"
                className="w-full"
              >
                요약 재처리 (수동)
              </Button>
            </div>
          )}
          {content.status === 'SUMMARIZING' && (
            <p className="text-muted-foreground">LLM이 요약을 생성하는 중입니다. 잠시만 기다려 주세요.</p>
          )}
          {content.status === 'SUMMARY_FAILED' && (
            <div className="space-y-4">
              <p className="text-destructive">요약 생성에 실패했습니다. 다시 시도하려면 아래 버튼을 클릭하세요.</p>
              <Button
                type="button"
                onClick={() => handleRetry('summary')}
                variant="secondary"
                className="w-full"
              >
                LLM 요약 재처리
              </Button>
            </div>
          )}
          {content.summary_md ? (
            <MarkdownContent content={content.summary_md} />
          ) : (
            content.status !== 'SUMMARY_QUEUED' &&
            content.status !== 'SUMMARIZING' &&
            content.status !== 'SUMMARY_FAILED' && (
              <p className="text-muted-foreground">요약이 아직 준비되지 않았습니다.</p>
            )
          )}
        </CardContent>
      </Card>

      {(content.status === 'ASR_FAILED' || content.status === 'PROCESSING' || content.status === 'QUEUED' ||
        content.status === 'OCR_FAILED' || content.status === 'OCR_PROCESSING') && (
          <Card className={cn(
            (content.status === 'ASR_FAILED' || content.status === 'OCR_FAILED') && "border-destructive",
            content.status === 'QUEUED' && "border-primary"
          )}>
            <CardHeader>
              <CardTitle className={cn(
                "text-base",
                (content.status === 'ASR_FAILED' || content.status === 'OCR_FAILED') && "text-destructive",
                content.status === 'QUEUED' && "text-primary"
              )}>
                {content.status === 'ASR_FAILED' || content.status === 'OCR_FAILED'
                  ? `${isDocumentFile(content.filename) ? '문서' : '음성'} 인식이 실패했습니다. 아래 버튼을 클릭하여 재처리하세요.`
                  : content.status === 'QUEUED'
                    ? `${isDocumentFile(content.filename) ? '문서' : '음성'} 인식이 대기 중입니다. 재시도하려면 아래 버튼을 클릭하세요.`
                    : `${isDocumentFile(content.filename) ? '문서' : '음성'} 인식이 진행 중입니다. 재시도하려면 아래 버튼을 클릭하세요.`}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {!isDocumentFile(content.filename) && (
                <>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label htmlFor="minSpeakers">최소 화자 수 (선택사항)</Label>
                      <Input
                        id="minSpeakers"
                        type="number"
                        min="1"
                        value={minSpeakers}
                        onChange={(e) => setMinSpeakers(e.target.value)}
                        placeholder="자동 결정"
                      />
                    </div>
                    <div className="space-y-2">
                      <Label htmlFor="maxSpeakers">최대 화자 수 (선택사항)</Label>
                      <Input
                        id="maxSpeakers"
                        type="number"
                        min="1"
                        value={maxSpeakers}
                        onChange={(e) => setMaxSpeakers(e.target.value)}
                        placeholder="자동 결정"
                      />
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    화자 수 범위를 지정하지 않으면 자동으로 결정됩니다.
                  </p>
                </>
              )}
              <Button
                type="button"
                onClick={() => handleRetry(isDocumentFile(content.filename) ? 'ocr' : 'asr')}
                variant="default"
                className="w-full"
              >
                {isDocumentFile(content.filename) ? 'OCR 재처리' : 'ASR 재처리'}
              </Button>
            </CardContent>
          </Card>
        )}

      {message && (
        <div className={cn(
          "p-3 rounded-md text-sm",
          message.includes('실패') ? "bg-destructive/10 text-destructive" : "bg-primary/10 text-primary"
        )}>
          {message}
        </div>
      )}

      {/* 오디오 타입: 세그먼트 표시 */}
      {content.transcription && content.transcription.segments && (
        <Card>
          <CardHeader>
            <CardTitle>세그먼트</CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea
              ref={segmentContainerRef}
              viewportRef={segmentViewportRef}
              className="h-[700px] md:h-[850px] rounded-lg border px-1 py-4"
            >
              <div className="space-y-4">
                {content.transcription.segments.map((seg) => {
                  const isActive = currentSegmentId === seg.id
                  return (
                    <div
                      key={seg.id}
                      id={`segment-${seg.id}`}
                      className={cn(
                        "pb-4 border-b last:border-b-0 transition-colors rounded-md px-2 py-3",
                        isActive && "bg-primary/10"
                      )}
                    >
                      <div className="flex items-center gap-2 mb-2">
                        <Badge variant="outline">{seg.speaker || 'UNKNOWN'}</Badge>
                        <button
                          onClick={() => handleSeekToTime(seg.start)}
                          className="text-xs text-muted-foreground hover:text-primary underline underline-offset-2 transition-colors"
                        >
                          [{seg.start.toFixed(2)}s - {seg.end.toFixed(2)}s]
                        </button>
                      </div>
                      <p className="text-base leading-relaxed break-words">{seg.text}</p>
                    </div>
                  )
                })}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      )}

      {/* 문서 타입: OCR 결과 표시 */}
      {content.document && (
        <Card>
          <CardHeader>
            <CardTitle>{isTxtFile(content.filename) ? '문서 내용' : 'OCR 결과'}</CardTitle>
            <CardDescription>
              {isTxtFile(content.filename)
                ? '텍스트 파일 내용'
                : `페이지 수: ${content.document.page_count}페이지`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[700px] md:h-[850px] rounded-lg border px-4 py-4">
              {content.document.ocr_text || content.document.html_content ? (
                isTxtFile(content.filename) ? (
                  // 텍스트 파일은 기존 방식 유지 (monospace)
                  <div className="whitespace-pre-wrap break-words text-base leading-relaxed font-mono">
                    {content.document.ocr_text}
                  </div>
                ) : content.document.html_content ? (
                  // Docling HTML 콘텐츠가 있으면 우선적으로 렌더링 (뷰어용)
                  <div className="doc-viewer">
                    <HtmlContent content={content.document.html_content} />
                  </div>
                ) : content.document.ocr_text ? (
                  // OCR 텍스트만 있으면 마크다운으로 렌더링 (기본 모드)
                  // JSON 문자열인지 확인 (Docling fallback인 경우)
                  content.document.ocr_text.trim().startsWith('{') && content.document.ocr_text.trim().startsWith('{"schema_name') ? (
                    <div className="text-muted-foreground p-4 border rounded">
                      <p className="font-semibold mb-2">OCR 처리 중 오류가 발생했습니다.</p>
                      <p className="text-sm">원본 JSON 데이터가 표시되고 있습니다. OCR 처리를 다시 시도해주세요.</p>
                    </div>
                  ) : (
                    // 무조건 HTML 뷰어로 렌더링
                    <div className="doc-viewer">
                      <HtmlContent content={content.document.ocr_text} />
                    </div>
                  )
                ) : null
              ) : (
                <p className="text-muted-foreground">
                  {isTxtFile(content.filename) ? '내용이 없습니다.' : 'OCR 결과가 없습니다.'}
                </p>
              )}
            </ScrollArea>
          </CardContent>
        </Card>
      )}

      {content.transcription?.diarization_metadata && (
        <Card>
          <CardHeader>
            <CardTitle>화자 분리 메타데이터</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-sm">
              <strong>구분된 화자 수:</strong> {content.transcription.diarization_metadata.num_speakers}명
            </p>
            <p className="text-sm">
              <strong>화자 라벨:</strong> {content.transcription.diarization_metadata.speaker_labels.join(', ')}
            </p>
          </CardContent>
        </Card>
      )}

      {content.transcription?.diarization_metadata?.segment_embeddings &&
        content.transcription.diarization_metadata.segment_embeddings.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>화자 재분류</CardTitle>
              <CardDescription>
                저장된 세그먼트 임베딩을 기반으로 화자를 재클러스터링합니다. GPU 연산 없이 빠르게 처리됩니다.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="numSpeakers">화자 수 (선택사항)</Label>
                <Input
                  id="numSpeakers"
                  type="number"
                  min="1"
                  value={numSpeakers}
                  onChange={(e) => setNumSpeakers(e.target.value)}
                  placeholder="자동 결정"
                  className="max-w-xs"
                  disabled={isReclustering}
                />
                <p className="text-xs text-muted-foreground">
                  비워두면 자동으로 결정됩니다.
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="similarityThreshold">
                  유사도 임계값: {similarityThreshold.toFixed(2)}
                </Label>
                <input
                  id="similarityThreshold"
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={similarityThreshold}
                  onChange={(e) => setSimilarityThreshold(parseFloat(e.target.value))}
                  className="w-full max-w-md"
                  disabled={isReclustering}
                />
                <p className="text-xs text-muted-foreground">
                  0.0 (낮음) ~ 1.0 (높음) - 유사도가 이 값 이상인 세그먼트를 같은 화자로 묶습니다.
                </p>
              </div>
              <Button
                onClick={handleRecluster}
                disabled={isReclustering}
                variant="secondary"
                className="w-full"
              >
                {isReclustering ? '재클러스터링 중...' : '재클러스터링 실행'}
              </Button>
              {reclusterMessage && (
                <div className={cn(
                  "p-3 rounded-md text-sm",
                  reclusterMessage.includes('✅')
                    ? "bg-primary/10 text-primary"
                    : "bg-destructive/10 text-destructive"
                )}>
                  {reclusterMessage}
                </div>
              )}
            </CardContent>
          </Card>
        )}

      <Card>
        <CardHeader>
          <CardTitle>로그</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {content.logs?.map((log) => (
            <div key={log.id} className="pb-4 border-b last:border-b-0">
              <p className="text-sm font-semibold mb-2">{log.message || '로그'}</p>
              <pre className="text-xs overflow-x-auto break-words whitespace-pre-wrap bg-muted p-3 rounded-md">
                {JSON.stringify(log.log, null, 2)}
              </pre>
              <p className="text-xs text-muted-foreground mt-2">{formatToKST(log.created_at)}</p>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>LLM 로그</CardTitle>
        </CardHeader>
        <CardContent>
          {content.llm_logs?.length ? (
            <div className="space-y-4">
              {content.llm_logs.map((log) => (
                <div key={log.id} className="pb-4 border-b last:border-b-0">
                  <p className="text-sm font-semibold mb-2">{log.message || '로그'}</p>
                  <pre className="text-xs overflow-x-auto break-words whitespace-pre-wrap bg-muted p-3 rounded-md">
                    {JSON.stringify(log.log, null, 2)}
                  </pre>
                  <p className="text-xs text-muted-foreground mt-2">{formatToKST(log.created_at)}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-muted-foreground">LLM 로그가 없습니다.</p>
          )}
        </CardContent>
      </Card>

      {showScrollTop && (
        <Button
          type="button"
          onClick={handleScrollToTop}
          size="icon"
          className="fixed bottom-8 left-1/2 -translate-x-1/2 w-12 h-12 rounded-full shadow-lg z-[1000] hidden md:flex"
          aria-label="맨 위로"
        >
          <ArrowUp className="h-5 w-5" />
        </Button>
      )}

      {/* OCR 재처리 모달 */}
      <Dialog open={showOcrRetryModal} onOpenChange={setShowOcrRetryModal}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>OCR 재처리 옵션</DialogTitle>
            <DialogDescription>
              *문서의 특성에 맞는 처리 방식을 선택하세요.
            </DialogDescription>
          </DialogHeader>

          <div className="py-4">
            <RadioGroup
              value={ocrRetryMode || ''}
              onValueChange={(value) => setOcrRetryMode(value as 'portray' | 'document')}
            >
              <div className="space-y-3">
                <Label
                  htmlFor="retry-portray"
                  className={`flex flex-col space-y-1 rounded-md border border-input bg-background p-4 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary ${isOfficeFile(content.filename) || isPdfFile(content.filename) ? 'opacity-50 cursor-not-allowed' : ''
                    }`}
                >
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem
                      value="portray"
                      id="retry-portray"
                      disabled={isOfficeFile(content.filename) || isPdfFile(content.filename)}
                    />
                    <span className="text-sm font-semibold">이미지 묘사</span>
                  </div>
                  <p className="text-xs text-muted-foreground ml-6">
                    전문적인 시각으로 이미지의 대상, 인물, 상황을 분석하고 상세하게 묘사합니다. (이미지 파일 전용)
                  </p>
                </Label>
                <Label
                  htmlFor="retry-document"
                  className="flex flex-col space-y-1 rounded-md border border-input bg-background p-4 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                >
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="document" id="retry-document" />
                    <span className="text-sm font-semibold">문서 분석</span>
                  </div>
                  <p className="text-xs text-muted-foreground ml-6">
                    Qwen3-VL 모델을 사용하여 문서의 텍스트와 구조를 심층적으로 분석합니다.
                    (일반 문서, 표가 포함된 문서에 권장)
                  </p>
                </Label>
              </div>
            </RadioGroup>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setShowOcrRetryModal(false)}
            >
              취소
            </Button>
            <Button
              type="button"
              onClick={handleOcrRetryConfirm}
              disabled={!ocrRetryMode}
            >
              재처리 시작
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
