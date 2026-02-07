/**
 * Renderer 패턴 구현
 * Open/Closed 원칙 - 새로운 콘텐츠 타입 추가 시 기존 코드 수정 없이 확장 가능
 */

import type { ReactNode } from 'react'
import type { ContentDetail, ContentType } from '../../types'

export interface RendererProps {
  content: ContentDetail
}

// Renderer 인터페이스
export interface ContentRenderer {
  (props: RendererProps): ReactNode
}

// Document Renderer
function DocumentRenderer({ content }: RendererProps) {
  const { document, summary, summary_html } = content

  return (
    <div className="space-y-6">
      {/* 요약 */}
      {summary_html ? (
        <div className="prose dark:prose-invert max-w-none">
          <h3 className="text-lg font-semibold mb-4">요약</h3>
          <div dangerouslySetInnerHTML={{ __html: summary_html }} />
        </div>
      ) : summary ? (
        <div className="prose dark:prose-invert max-w-none">
          <h3 className="text-lg font-semibold mb-4">요약</h3>
          <p className="whitespace-pre-wrap">{summary}</p>
        </div>
      ) : null}

      {/* 문서 정보 */}
      {document && (
        <div className="text-sm text-muted-foreground">
          <p>총 {document.page_count}페이지</p>
          {document.text_content && (
            <details className="mt-4">
              <summary className="cursor-pointer hover:text-foreground">
                원본 텍스트 보기
              </summary>
              <pre className="mt-2 p-4 bg-muted rounded-lg overflow-x-auto text-xs">
                {document.text_content}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  )
}

// Audio Renderer
function AudioRenderer({ content }: RendererProps) {
  const { transcription, summary, summary_html, file_url } = content

  return (
    <div className="space-y-6">
      {/* 오디오 플레이어 */}
      {file_url && (
        <audio controls className="w-full">
          <source src={file_url} />
          브라우저가 오디오를 지원하지 않습니다.
        </audio>
      )}

      {/* 요약 */}
      {summary_html ? (
        <div className="prose dark:prose-invert max-w-none">
          <h3 className="text-lg font-semibold mb-4">요약</h3>
          <div dangerouslySetInnerHTML={{ __html: summary_html }} />
        </div>
      ) : summary ? (
        <div className="prose dark:prose-invert max-w-none">
          <h3 className="text-lg font-semibold mb-4">요약</h3>
          <p className="whitespace-pre-wrap">{summary}</p>
        </div>
      ) : null}

      {/* 전사 세그먼트 */}
      {transcription?.segments && transcription.segments.length > 0 && (
        <div>
          <h3 className="text-lg font-semibold mb-4">
            전사 내용 ({transcription.segments.length}개 세그먼트)
          </h3>
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {transcription.segments.map((segment) => (
              <div
                key={segment.id}
                className="flex gap-3 p-3 bg-muted/50 rounded-lg text-sm"
              >
                <span className="text-muted-foreground whitespace-nowrap">
                  [{formatTime(segment.start)} - {formatTime(segment.end)}]
                </span>
                {segment.speaker && (
                  <span className="font-medium text-primary">{segment.speaker}:</span>
                )}
                <span>{segment.text}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Portray (이미지) Renderer
function PortrayRenderer({ content }: RendererProps) {
  const { file_url, summary, summary_html } = content

  return (
    <div className="space-y-6">
      {/* 이미지 */}
      {file_url && (
        <div className="flex justify-center">
          <img
            src={file_url}
            alt={content.title || content.filename}
            className="max-w-full max-h-[600px] object-contain rounded-lg"
          />
        </div>
      )}

      {/* 요약 */}
      {summary_html ? (
        <div className="prose dark:prose-invert max-w-none">
          <h3 className="text-lg font-semibold mb-4">분석 결과</h3>
          <div dangerouslySetInnerHTML={{ __html: summary_html }} />
        </div>
      ) : summary ? (
        <div className="prose dark:prose-invert max-w-none">
          <h3 className="text-lg font-semibold mb-4">분석 결과</h3>
          <p className="whitespace-pre-wrap">{summary}</p>
        </div>
      ) : null}
    </div>
  )
}

// 시간 포맷팅 유틸
function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

// Renderer 레지스트리 (Open/Closed 원칙)
export const renderers: Record<ContentType, ContentRenderer> = {
  DOCUMENT: DocumentRenderer,
  AUDIO: AudioRenderer,
  PORTRAY: PortrayRenderer,
}

// 메인 Renderer 컴포넌트
export function ContentRenderer({ content }: RendererProps) {
  const Renderer = renderers[content.content_type]

  if (!Renderer) {
    return (
      <div className="text-muted-foreground">
        지원하지 않는 콘텐츠 타입입니다: {content.content_type}
      </div>
    )
  }

  return <Renderer content={content} />
}
