import ReactMarkdown from 'react-markdown'

import { ContentDetail as ContentDetailType } from '@/lib/api'

type Props = {
  content: ContentDetailType
}

export default function ContentDetail({ content }: Props) {
  return (
    <div className="card">
      <h2>{content.filename}</h2>
      <p>
        총 재생 길이 {content.duration_seconds.toFixed(1)}초 · 화자 {content.speakers.join(', ') || '분석 중'}
      </p>
      <p>저장 키: {content.object_key}</p>
      <section>
        <h3>LLM 요약</h3>
        {content.status === 'SUMMARIZING' && <p>LLM이 요약을 생성하는 중입니다. 잠시만 기다려 주세요.</p>}
        {content.status === 'SUMMARY_FAILED' && (
          <p style={{ color: '#E53935' }}>요약 생성에 실패했습니다. 다시 시도하려면 콘텐츠를 재처리하세요.</p>
        )}
        {content.summary_md ? (
          <ReactMarkdown>{content.summary_md}</ReactMarkdown>
        ) : (
          content.status !== 'SUMMARIZING' &&
          content.status !== 'SUMMARY_FAILED' && <p>요약이 아직 준비되지 않았습니다.</p>
        )}
      </section>
      <section>
        <h3>세그먼트</h3>
        {content.transcription.segments?.map((seg) => (
          <div key={seg.id} className="segment">
            <strong>{seg.speaker || 'UNKNOWN'}</strong> [{seg.start.toFixed(2)}s - {seg.end.toFixed(2)}s]
            <p>{seg.text}</p>
          </div>
        ))}
      </section>
      <section>
        <h3>로그</h3>
        {content.logs?.map((log) => (
          <div key={log.id} className="segment">
            <strong>{log.message}</strong>
            <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(log.log, null, 2)}</pre>
            <small>{new Date(log.created_at).toLocaleString()}</small>
          </div>
        ))}
      </section>
      <section>
        <h3>LLM 로그</h3>
        {content.llm_logs?.length ? (
          content.llm_logs.map((log) => (
            <div key={log.id} className="segment">
              <strong>{log.message || '로그'}</strong>
              <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(log.log, null, 2)}</pre>
              <small>{new Date(log.created_at).toLocaleString()}</small>
            </div>
          ))
        ) : (
          <p>LLM 로그가 없습니다.</p>
        )}
      </section>
    </div>
  )
}

