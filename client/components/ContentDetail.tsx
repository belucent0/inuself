'use client'

import ReactMarkdown from 'react-markdown'
import { useState, useRef, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { ArrowUp, Download } from 'lucide-react'

import { ContentDetail as ContentDetailType, retryProcessing, reclusterSpeakers } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatToKST } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'

type Props = {
  content: ContentDetailType
}

// 오디오 파일인지 확인하는 헬퍼 함수
function isAudioFile(filename: string): boolean {
  const audioExtensions = ['.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.wma']
  const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
  return audioExtensions.includes(ext)
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
  const previousSegmentIdRef = useRef<number | null>(null)
  const audioRef = useRef<HTMLAudioElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const segmentContainerRef = useRef<HTMLDivElement>(null)
  const segmentViewportRef = useRef<HTMLDivElement | null>(null)
  
  const handleRetry = async (type: 'asr' | 'summary') => {
    const typeLabel = type === 'asr' ? 'ASR 처리' : 'LLM 요약'
    if (!confirm(`${typeLabel}를 다시 시도하시겠습니까?`)) {
      return
    }
    
    try {
      let minSpeakersValue: number | undefined = undefined
      let maxSpeakersValue: number | undefined = undefined
      
      if (type === 'asr') {
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
  
  const handleRecluster = async () => {
    if (!content.transcription.diarization_metadata?.segment_embeddings || 
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
    const mediaElement = isAudioFile(content.filename) ? audioRef.current : videoRef.current
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
    if (!content.media_url || !content.transcription.segments) {
      return
    }

    const mediaElement = isAudioFile(content.filename) ? audioRef.current : videoRef.current
    if (!mediaElement) {
      return
    }

    const handleTimeUpdate = () => {
      const currentTime = mediaElement.currentTime
      const activeSegment = content.transcription.segments?.find(
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
  }, [content.media_url, content.filename, content.transcription.segments, autoScroll])
  
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-xl break-words">{content.title || content.filename}</CardTitle>
          <CardDescription>
            총 재생 길이 {content.duration_seconds.toFixed(1)}초 · 화자 {content.speakers.join(', ') || '분석 중'}
          </CardDescription>
          <p className="text-xs text-muted-foreground break-all mt-2">저장 키: {content.object_key}</p>
        </CardHeader>
        
        {content.media_url && (
          <CardContent className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold mb-2">미디어 재생</h3>
              {isAudioFile(content.filename) ? (
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
                <video 
                  ref={videoRef}
                  controls 
                  src={content.media_url} 
                  className="w-full max-h-[500px]"
                  preload="metadata"
                >
                  브라우저가 비디오 재생을 지원하지 않습니다.
                </video>
              )}
            </div>
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex items-center gap-2">
                <Switch
                  checked={autoScroll}
                  onCheckedChange={setAutoScroll}
                  className="touch-manipulation"
                  aria-label={autoScroll ? '자동 스크롤 활성화' : '자동 스크롤 비활성화'}
                />
                <Label className="text-sm">스크립트 자동 스크롤</Label>
              </div>
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
            <div className="markdown-content">
              <ReactMarkdown>{content.summary_md}</ReactMarkdown>
            </div>
          ) : (
            content.status !== 'SUMMARIZING' &&
            content.status !== 'SUMMARY_FAILED' && (
              <p className="text-muted-foreground">요약이 아직 준비되지 않았습니다.</p>
            )
          )}
        </CardContent>
      </Card>

      {(content.status === 'ASR_FAILED' || content.status === 'PROCESSING' || content.status === 'QUEUED') && (
        <Card className={cn(
          content.status === 'ASR_FAILED' && "border-destructive",
          content.status === 'QUEUED' && "border-primary"
        )}>
          <CardHeader>
            <CardTitle className={cn(
              "text-base",
              content.status === 'ASR_FAILED' && "text-destructive",
              content.status === 'QUEUED' && "text-primary"
            )}>
              {content.status === 'ASR_FAILED' 
                ? 'ASR 처리가 실패했습니다. 아래 버튼을 클릭하여 재처리하세요.'
                : content.status === 'QUEUED'
                ? 'ASR 처리가 대기 중입니다. 재시도하려면 아래 버튼을 클릭하세요.'
                : 'ASR 처리가 진행 중입니다. 재시도하려면 아래 버튼을 클릭하세요.'}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
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
            <Button
              type="button"
              onClick={() => handleRetry('asr')}
              variant="default"
              className="w-full"
            >
              ASR 재처리
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
              {content.transcription.segments?.map((seg) => {
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

      {content.transcription.diarization_metadata && (
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

      {content.transcription.diarization_metadata?.segment_embeddings && 
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
    </div>
  )
}
