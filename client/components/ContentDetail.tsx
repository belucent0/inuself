'use client'

import ReactMarkdown from 'react-markdown'
import { useState, useRef, useEffect } from 'react'
import { useRouter } from 'next/navigation'

import { ContentDetail as ContentDetailType, retryProcessing, reclusterSpeakers } from '@/lib/api'
import { formatToKST } from '@/lib/utils'

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
          const container = segmentContainerRef.current
          if (segmentElement && container) {
            // 컨테이너 내부 스크롤로 이동
            const containerRect = container.getBoundingClientRect()
            const elementRect = segmentElement.getBoundingClientRect()
            const scrollTop = container.scrollTop
            const elementOffsetTop = elementRect.top - containerRect.top + scrollTop
            const containerCenter = container.clientHeight / 2
            const targetScrollTop = elementOffsetTop - containerCenter + (elementRect.height / 2)
            
            container.scrollTo({
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
    <div className="card">
      <h2 style={{ fontSize: '1.2rem', wordBreak: 'break-word', marginBottom: '0.5rem' }}>{content.title || content.filename}</h2>
      <p style={{ fontSize: '0.9rem', marginBottom: '0.5rem', lineHeight: '1.5' }}>
        총 재생 길이 {content.duration_seconds.toFixed(1)}초 · 화자 {content.speakers.join(', ') || '분석 중'}
      </p>
      <p style={{ fontSize: '0.85rem', color: '#666', marginBottom: '1rem', wordBreak: 'break-all' }}>저장 키: {content.object_key}</p>
      
      {content.media_url && (
        <section style={{ marginTop: '1rem', marginBottom: '1rem' }}>
          <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>미디어 재생</h3>
          {isAudioFile(content.filename) ? (
            <audio 
              ref={audioRef}
              controls 
              src={content.media_url} 
              style={{ width: '100%', maxWidth: '600px' }}
              preload="metadata"
            >
              브라우저가 오디오 재생을 지원하지 않습니다.
            </audio>
          ) : (
            <video 
              ref={videoRef}
              controls 
              src={content.media_url} 
              style={{ width: '100%', maxHeight: '500px' }}
              preload="metadata"
            >
              브라우저가 비디오 재생을 지원하지 않습니다.
            </video>
          )}
          <div style={{ marginTop: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <button
              type="button"
              onClick={() => setAutoScroll(!autoScroll)}
              style={{
                position: 'relative',
                width: '44px',
                height: '24px',
                backgroundColor: autoScroll ? '#000' : '#d1d5db',
                border: 'none',
                borderRadius: '12px',
                cursor: 'pointer',
                padding: '0',
                transition: 'background-color 0.2s'
              }}
              aria-label={autoScroll ? '자동 스크롤 활성화' : '자동 스크롤 비활성화'}
            >
              <span
                style={{
                  position: 'absolute',
                  top: '2px',
                  left: autoScroll ? '22px' : '2px',
                  width: '20px',
                  height: '20px',
                  backgroundColor: '#fff',
                  borderRadius: '50%',
                  transition: 'left 0.2s',
                  boxShadow: '0 1px 2px rgba(0, 0, 0, 0.1)'
                }}
              />
            </button>
            <span style={{ fontSize: '0.9rem', color: '#333' }}>
              스크립트 자동 스크롤
            </span>
          </div>
        </section>
      )}
      
      <section style={{ marginTop: '1.5rem' }}>
        <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>LLM 요약</h3>
        {content.status === 'SUMMARIZING' && <p>LLM이 요약을 생성하는 중입니다. 잠시만 기다려 주세요.</p>}
        {content.status === 'SUMMARY_FAILED' && (
          <div>
            <p style={{ color: '#E53935' }}>요약 생성에 실패했습니다. 다시 시도하려면 아래 버튼을 클릭하세요.</p>
            <button
              type="button"
              onClick={() => handleRetry('summary')}
              style={{
                padding: '0.5rem 1rem',
                backgroundColor: '#673AB7',
                color: '#fff',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                marginTop: '0.5rem',
                minHeight: '44px',
                width: '100%',
                fontSize: '0.9rem',
              }}
            >
              LLM 요약 재처리
            </button>
          </div>
        )}
        {content.summary_md ? (
          <ReactMarkdown>{content.summary_md}</ReactMarkdown>
        ) : (
          content.status !== 'SUMMARIZING' &&
          content.status !== 'SUMMARY_FAILED' && <p>요약이 아직 준비되지 않았습니다.</p>
        )}
      </section>
      {(content.status === 'ASR_FAILED' || content.status === 'PROCESSING' || content.status === 'QUEUED') && (
        <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: content.status === 'ASR_FAILED' ? '#ffebee' : content.status === 'QUEUED' ? '#e3f2fd' : '#fff3e0', borderRadius: '4px' }}>
          <p style={{ color: content.status === 'ASR_FAILED' ? '#E53935' : content.status === 'QUEUED' ? '#1976D2' : '#F57C00', marginBottom: '0.5rem' }}>
            {content.status === 'ASR_FAILED' 
              ? 'ASR 처리가 실패했습니다. 아래 버튼을 클릭하여 재처리하세요.'
              : content.status === 'QUEUED'
              ? 'ASR 처리가 대기 중입니다. 재시도하려면 아래 버튼을 클릭하세요.'
              : 'ASR 처리가 진행 중입니다. 재시도하려면 아래 버튼을 클릭하세요.'}
          </p>
          <div style={{ marginBottom: '1rem' }}>
            <div style={{ display: 'flex', gap: '1rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
              <div style={{ flex: '1', minWidth: '150px' }}>
                <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.85rem', fontWeight: 'bold' }}>
                  최소 화자 수 (선택사항):
                </label>
                <input
                  type="number"
                  min="1"
                  value={minSpeakers}
                  onChange={(e) => setMinSpeakers(e.target.value)}
                  placeholder="자동 결정"
                  style={{
                    width: '100%',
                    padding: '0.5rem',
                    border: '1px solid #ddd',
                    borderRadius: '4px',
                    fontSize: '0.9rem'
                  }}
                />
              </div>
              <div style={{ flex: '1', minWidth: '150px' }}>
                <label style={{ display: 'block', marginBottom: '0.25rem', fontSize: '0.85rem', fontWeight: 'bold' }}>
                  최대 화자 수 (선택사항):
                </label>
                <input
                  type="number"
                  min="1"
                  value={maxSpeakers}
                  onChange={(e) => setMaxSpeakers(e.target.value)}
                  placeholder="자동 결정"
                  style={{
                    width: '100%',
                    padding: '0.5rem',
                    border: '1px solid #ddd',
                    borderRadius: '4px',
                    fontSize: '0.9rem'
                  }}
                />
              </div>
            </div>
            <p style={{ fontSize: '0.8rem', color: '#999', marginTop: '0.25rem' }}>
              화자 수 범위를 지정하지 않으면 자동으로 결정됩니다.
            </p>
          </div>
          <button
            type="button"
            onClick={() => handleRetry('asr')}
            style={{
              padding: '0.5rem 1rem',
              backgroundColor: '#2196F3',
              color: '#fff',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              minHeight: '44px',
              width: '100%',
              fontSize: '0.9rem',
            }}
          >
            ASR 재처리
          </button>
        </div>
      )}
      {message && (
        <p style={{ marginTop: '1rem', color: message.includes('실패') ? '#F44336' : '#4CAF50' }}>
          {message}
        </p>
      )}
      <section style={{ marginTop: '1.5rem' }}>
        <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>세그먼트</h3>
        <div
          ref={segmentContainerRef}
          className="segment-container"
          style={{
            position: 'sticky',
            top: '1rem',
            maxHeight: '800px',
            overflowY: 'auto',
            border: '1px solid #e5e7eb',
            borderRadius: '8px',
            padding: '0.5rem',
            backgroundColor: '#fff',
            zIndex: 10
          }}
        >
          {content.transcription.segments?.map((seg) => {
            const isActive = currentSegmentId === seg.id
            return (
              <div 
                key={seg.id}
                id={`segment-${seg.id}`}
                className="segment"
                style={{
                  backgroundColor: isActive ? '#E3F2FD' : 'transparent',
                  borderRadius: isActive ? '4px' : undefined,
                  transition: 'background-color 0.2s ease',
                  padding: '0.75rem 0'
                }}
              >
                <strong style={{ fontSize: '0.9rem' }}>{seg.speaker || 'UNKNOWN'}</strong>{' '}
                <span 
                  onClick={() => handleSeekToTime(seg.start)}
                  style={{ 
                    fontSize: '0.85rem', 
                    color: '#666',
                    cursor: 'pointer',
                    textDecoration: 'underline',
                    textDecorationColor: '#999',
                    transition: 'color 0.2s ease'
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.color = '#2196F3'
                    e.currentTarget.style.textDecorationColor = '#2196F3'
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.color = '#666'
                    e.currentTarget.style.textDecorationColor = '#999'
                  }}
                >
                  [{seg.start.toFixed(2)}s - {seg.end.toFixed(2)}s]
                </span>
                <p style={{ marginTop: '0.25rem', fontSize: '0.9rem', lineHeight: '1.5', wordBreak: 'break-word' }}>{seg.text}</p>
              </div>
            )
          })}
        </div>
      </section>
      {content.transcription.diarization_metadata && (
        <section style={{ marginTop: '1.5rem' }}>
          <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>화자 분리 메타데이터</h3>
          <div style={{ padding: '1rem', backgroundColor: '#f5f5f5', borderRadius: '4px', fontSize: '0.9rem' }}>
            <p style={{ marginBottom: '0.5rem' }}>
              <strong>구분된 화자 수:</strong> {content.transcription.diarization_metadata.num_speakers}명
            </p>
            <p style={{ marginBottom: '0.5rem' }}>
              <strong>화자 라벨:</strong> {content.transcription.diarization_metadata.speaker_labels.join(', ')}
            </p>
          </div>
        </section>
      )}
      {content.transcription.diarization_metadata?.segment_embeddings && 
       content.transcription.diarization_metadata.segment_embeddings.length > 0 && (
        <section style={{ marginTop: '1.5rem' }}>
          <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>화자 재분류</h3>
          <div style={{ padding: '1rem', backgroundColor: '#f5f5f5', borderRadius: '4px', fontSize: '0.9rem' }}>
            <p style={{ marginBottom: '1rem', color: '#666', fontSize: '0.85rem' }}>
              저장된 세그먼트 임베딩을 기반으로 화자를 재클러스터링합니다. GPU 연산 없이 빠르게 처리됩니다.
            </p>
            <div style={{ marginBottom: '1rem' }}>
              <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 'bold' }}>
                화자 수 (선택사항):
              </label>
              <input
                type="number"
                min="1"
                value={numSpeakers}
                onChange={(e) => setNumSpeakers(e.target.value)}
                placeholder="자동 결정"
                style={{
                  width: '200px',
                  padding: '0.5rem',
                  border: '1px solid #ddd',
                  borderRadius: '4px',
                  fontSize: '0.9rem'
                }}
                disabled={isReclustering}
              />
              <p style={{ fontSize: '0.8rem', color: '#999', marginTop: '0.25rem' }}>
                비워두면 자동으로 결정됩니다.
              </p>
            </div>
            <div style={{ marginBottom: '1rem' }}>
              <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 'bold' }}>
                유사도 임계값: {similarityThreshold.toFixed(2)}
              </label>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={similarityThreshold}
                onChange={(e) => setSimilarityThreshold(parseFloat(e.target.value))}
                style={{ width: '100%', maxWidth: '400px' }}
                disabled={isReclustering}
              />
              <p style={{ fontSize: '0.8rem', color: '#999', marginTop: '0.25rem' }}>
                0.0 (낮음) ~ 1.0 (높음) - 유사도가 이 값 이상인 세그먼트를 같은 화자로 묶습니다.
              </p>
            </div>
            <button
              onClick={handleRecluster}
              disabled={isReclustering}
              style={{
                padding: '0.75rem 1.5rem',
                backgroundColor: isReclustering ? '#ccc' : '#673AB7',
                color: '#fff',
                border: 'none',
                borderRadius: '4px',
                fontSize: '0.9rem',
                cursor: isReclustering ? 'not-allowed' : 'pointer',
                fontWeight: 'bold'
              }}
            >
              {isReclustering ? '재클러스터링 중...' : '재클러스터링 실행'}
            </button>
            {reclusterMessage && (
              <p style={{ 
                marginTop: '1rem', 
                padding: '0.75rem',
                backgroundColor: reclusterMessage.includes('✅') ? '#e8f5e9' : '#ffebee',
                color: reclusterMessage.includes('✅') ? '#2e7d32' : '#c62828',
                borderRadius: '4px',
                fontSize: '0.85rem'
              }}>
                {reclusterMessage}
              </p>
            )}
          </div>
        </section>
      )}
      <section style={{ marginTop: '1.5rem' }}>
        <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>로그</h3>
        {content.logs?.map((log) => (
            <div key={log.id} className="segment">
              <strong style={{ fontSize: '0.9rem' }}>{log.message || '로그'}</strong>
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.8rem', overflowX: 'auto', wordBreak: 'break-word' }}>{JSON.stringify(log.log, null, 2)}</pre>
              <small style={{ fontSize: '0.85rem', color: '#666' }}>{formatToKST(log.created_at)}</small>
            </div>
        ))}
      </section>
      <section style={{ marginTop: '1.5rem' }}>
        <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>LLM 로그</h3>
        {content.llm_logs?.length ? (
          content.llm_logs.map((log) => (
            <div key={log.id} className="segment">
              <strong style={{ fontSize: '0.9rem' }}>{log.message || '로그'}</strong>
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: '0.8rem', overflowX: 'auto', wordBreak: 'break-word' }}>{JSON.stringify(log.log, null, 2)}</pre>
              <small style={{ fontSize: '0.85rem', color: '#666' }}>{formatToKST(log.created_at)}</small>
            </div>
          ))
        ) : (
          <p>LLM 로그가 없습니다.</p>
        )}
      </section>
      {showScrollTop && (
        <button
          type="button"
          onClick={handleScrollToTop}
          style={{
            position: 'fixed',
            bottom: '2rem',
            left: '50%',
            transform: 'translateX(-50%)',
            width: '48px',
            height: '48px',
            backgroundColor: '#111827',
            color: '#fff',
            border: 'none',
            borderRadius: '50%',
            cursor: 'pointer',
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '20px',
            zIndex: 1000,
            transition: 'opacity 0.3s ease, transform 0.2s ease'
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.opacity = '0.9'
            e.currentTarget.style.transform = 'translateX(-50%) translateY(-2px)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.opacity = '1'
            e.currentTarget.style.transform = 'translateX(-50%) translateY(0)'
          }}
          aria-label="맨 위로"
        >
          ↑
        </button>
      )}
    </div>
  )
}

