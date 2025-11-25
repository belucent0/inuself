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
    </div>
  )
}

