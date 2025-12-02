'use client'

import ReactMarkdown from 'react-markdown'
import { useState } from 'react'
import { useRouter } from 'next/navigation'

import { ContentDetail as ContentDetailType, retryProcessing } from '@/lib/api'

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
  
  const handleRetry = async (type: 'asr' | 'summary') => {
    const typeLabel = type === 'asr' ? 'ASR 처리' : 'LLM 요약'
    if (!confirm(`${typeLabel}를 다시 시도하시겠습니까?`)) {
      return
    }
    
    try {
      const result = await retryProcessing(content.id, type)
      setMessage(result.message)
      router.refresh()
      setTimeout(() => setMessage(''), 3000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '재처리 실패')
    }
  }
  
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
              controls 
              src={content.media_url} 
              style={{ width: '100%', maxWidth: '600px' }}
              preload="metadata"
            >
              브라우저가 오디오 재생을 지원하지 않습니다.
            </audio>
          ) : (
            <video 
              controls 
              src={content.media_url} 
              style={{ width: '100%', maxHeight: '500px' }}
              preload="metadata"
            >
              브라우저가 비디오 재생을 지원하지 않습니다.
            </video>
          )}
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
      {content.status === 'ASR_FAILED' && (
        <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: '#ffebee', borderRadius: '4px' }}>
          <p style={{ color: '#E53935', marginBottom: '0.5rem' }}>
            ASR 처리가 실패했습니다. 다시 시도하려면 아래 버튼을 클릭하세요.
          </p>
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
        {content.transcription.segments?.map((seg) => (
          <div key={seg.id} className="segment">
            <strong style={{ fontSize: '0.9rem' }}>{seg.speaker || 'UNKNOWN'}</strong> <span style={{ fontSize: '0.85rem', color: '#666' }}>[{seg.start.toFixed(2)}s - {seg.end.toFixed(2)}s]</span>
            <p style={{ marginTop: '0.25rem', fontSize: '0.9rem', lineHeight: '1.5', wordBreak: 'break-word' }}>{seg.text}</p>
          </div>
        ))}
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
            {content.transcription.diarization_metadata.speaker_embeddings && (
              <div style={{ marginTop: '1rem' }}>
                <strong style={{ display: 'block', marginBottom: '0.5rem' }}>화자별 임베딩 벡터:</strong>
                {Object.entries(content.transcription.diarization_metadata.speaker_embeddings).map(([speaker, embedding]) => (
                  <div key={speaker} style={{ marginBottom: '1rem', padding: '0.75rem', backgroundColor: '#fff', borderRadius: '4px', border: '1px solid #ddd' }}>
                    <strong style={{ fontSize: '0.9rem', color: '#673AB7' }}>{speaker}</strong>
                    <p style={{ fontSize: '0.85rem', color: '#666', marginTop: '0.25rem', marginBottom: '0.5rem' }}>
                      차원: {embedding.length}
                    </p>
                    <details style={{ fontSize: '0.8rem' }}>
                      <summary style={{ cursor: 'pointer', color: '#2196F3', marginBottom: '0.25rem' }}>
                        임베딩 벡터 보기/숨기기
                      </summary>
                      <pre style={{ 
                        marginTop: '0.5rem', 
                        padding: '0.5rem', 
                        backgroundColor: '#fafafa', 
                        borderRadius: '4px', 
                        overflowX: 'auto',
                        fontSize: '0.75rem',
                        maxHeight: '200px',
                        overflowY: 'auto',
                        wordBreak: 'break-all',
                        whiteSpace: 'pre-wrap'
                      }}>
                        {JSON.stringify(embedding, null, 2)}
                      </pre>
                    </details>
                  </div>
                ))}
              </div>
            )}
            {content.transcription.diarization_metadata.segment_embeddings && content.transcription.diarization_metadata.segment_embeddings.length > 0 ? (
              <div style={{ marginTop: '1.5rem' }}>
                <strong style={{ display: 'block', marginBottom: '0.5rem' }}>시간대별 세그먼트 임베딩 벡터:</strong>
                <p style={{ fontSize: '0.85rem', color: '#666', marginBottom: '0.75rem' }}>
                  총 {content.transcription.diarization_metadata.segment_embeddings.length}개 세그먼트
                </p>
                <div style={{ maxHeight: '400px', overflowY: 'auto', border: '1px solid #ddd', borderRadius: '4px', padding: '0.5rem' }}>
                  {content.transcription.diarization_metadata.segment_embeddings.map((segEmb, idx) => (
                    <div key={idx} style={{ marginBottom: '0.75rem', padding: '0.75rem', backgroundColor: '#fff', borderRadius: '4px', border: '1px solid #eee' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                        <strong style={{ fontSize: '0.9rem', color: '#673AB7' }}>{segEmb.speaker}</strong>
                        <span style={{ fontSize: '0.85rem', color: '#666' }}>
                          {segEmb.start.toFixed(2)}s - {segEmb.end.toFixed(2)}s ({segEmb.duration.toFixed(2)}s)
                        </span>
                      </div>
                      <p style={{ fontSize: '0.85rem', color: '#666', marginBottom: '0.5rem' }}>
                        임베딩 차원: {segEmb.embedding.length}
                      </p>
                      <details style={{ fontSize: '0.8rem' }}>
                        <summary style={{ cursor: 'pointer', color: '#2196F3', marginBottom: '0.25rem' }}>
                          임베딩 벡터 보기/숨기기
                        </summary>
                        <pre style={{ 
                          marginTop: '0.5rem', 
                          padding: '0.5rem', 
                          backgroundColor: '#fafafa', 
                          borderRadius: '4px', 
                          overflowX: 'auto',
                          fontSize: '0.75rem',
                          maxHeight: '200px',
                          overflowY: 'auto',
                          wordBreak: 'break-all',
                          whiteSpace: 'pre-wrap'
                        }}>
                          {JSON.stringify(segEmb.embedding, null, 2)}
                        </pre>
                      </details>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div style={{ marginTop: '1.5rem' }}>
                <strong style={{ display: 'block', marginBottom: '0.5rem' }}>시간대별 세그먼트 임베딩 벡터:</strong>
                <p style={{ fontSize: '0.85rem', color: '#999', fontStyle: 'italic' }}>
                  시간대별 세그먼트 임베딩이 추출되지 않았습니다. (백엔드 로그에서 원인 확인 가능)
                </p>
              </div>
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
              <small style={{ fontSize: '0.85rem', color: '#666' }}>{new Date(log.created_at).toLocaleString()}</small>
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
              <small style={{ fontSize: '0.85rem', color: '#666' }}>{new Date(log.created_at).toLocaleString()}</small>
            </div>
          ))
        ) : (
          <p>LLM 로그가 없습니다.</p>
        )}
      </section>
    </div>
  )
}

