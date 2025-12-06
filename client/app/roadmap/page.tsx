'use client'

import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'

type TabType = 'developing' | 'considering' | 'longterm'

export default function RoadmapPage() {
  const [activeTab, setActiveTab] = useState<TabType>('developing')
  const [markdown, setMarkdown] = useState<string>('')
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const tabFiles: Record<TabType, string> = {
    developing: '/ROADMAP_DEVELOPING.md',
    considering: '/ROADMAP_CONSIDERING.md',
    longterm: '/ROADMAP_LONGTERM.md',
  }

  const tabLabels: Record<TabType, string> = {
    developing: '🔨 개발 중',
    considering: '💭 고려 중',
    longterm: '🎯 장기 도전',
  }

  useEffect(() => {
    const fetchMarkdown = async () => {
      setIsLoading(true)
      setError(null)
      try {
        const response = await fetch(tabFiles[activeTab])
        if (!response.ok) {
          throw new Error('로드맵 파일을 불러올 수 없습니다.')
        }
        const text = await response.text()
        setMarkdown(text)
      } catch (err) {
        setError(err instanceof Error ? err.message : '로드맵을 불러오는데 실패했습니다.')
      } finally {
        setIsLoading(false)
      }
    }

    fetchMarkdown()
  }, [activeTab])

  return (
    <section>
      <h2>로드맵</h2>
      <div
        style={{
          backgroundColor: 'white',
          padding: '1.5rem',
          borderRadius: '12px',
          boxShadow: '0 2px 8px rgba(15, 23, 42, 0.08)',
          width: '100%',
          boxSizing: 'border-box',
        }}
      >
        {/* 탭 버튼 */}
        <div className="roadmap-tabs" style={{ width: '100%' }}>
          {(['developing', 'considering', 'longterm'] as TabType[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`roadmap-tab ${activeTab === tab ? 'active' : ''}`}
            >
              {tabLabels[tab]}
            </button>
          ))}
        </div>

        {/* 콘텐츠 영역 */}
        {isLoading ? (
          <p>로딩 중...</p>
        ) : error ? (
          <p style={{ color: '#F44336' }}>{error}</p>
        ) : (
          <>
            <style jsx global>{`
              .roadmap-tabs {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 0.5rem;
                margin-bottom: 1.5rem;
                border-bottom: 2px solid #e5e7eb;
                width: 100%;
                box-sizing: border-box;
              }
              .roadmap-tab {
                padding: 0.75rem 1.5rem;
                background-color: transparent;
                color: #666;
                border: none;
                border-bottom: 2px solid transparent;
                cursor: pointer;
                font-size: 1rem;
                font-weight: 400;
                transition: background-color 0.2s, color 0.2s, border-bottom-color 0.2s;
                margin-bottom: -2px;
                white-space: nowrap;
                text-align: center;
                box-sizing: border-box;
                overflow: hidden;
                text-overflow: ellipsis;
              }
              .roadmap-tab.active {
                background-color: #2196F3;
                color: #fff;
                border-bottom: 2px solid #2196F3;
                font-weight: 600;
              }
              @media (max-width: 768px) {
                .roadmap-tabs {
                  gap: 0.25rem;
                }
                .roadmap-tab {
                  padding: 0.5rem 0.5rem;
                  font-size: 0.9rem;
                }
              }
              .roadmap-content {
                line-height: 1.6;
                color: #333;
              }
              .roadmap-content h1 {
                font-size: 2rem;
                margin-bottom: 1.5rem;
                color: #111;
                border-bottom: 2px solid #2196F3;
                padding-bottom: 0.5rem;
              }
              .roadmap-content h2 {
                font-size: 1.5rem;
                margin-top: 2rem;
                margin-bottom: 1rem;
                color: #2196F3;
              }
              .roadmap-content h3 {
                font-size: 1.25rem;
                margin-top: 1.5rem;
                margin-bottom: 0.75rem;
                color: #444;
              }
              .roadmap-content p {
                margin-bottom: 1rem;
              }
              .roadmap-content ul,
              .roadmap-content ol {
                margin-bottom: 1rem;
                padding-left: 2rem;
              }
              .roadmap-content li {
                margin-bottom: 0.5rem;
              }
              .roadmap-content table {
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 1.5rem;
              }
              .roadmap-content table th,
              .roadmap-content table td {
                padding: 0.75rem;
                border: 1px solid #ddd;
                text-align: left;
              }
              .roadmap-content table th {
                background-color: #f5f5f5;
                font-weight: 600;
              }
              .roadmap-content table tr:nth-child(even) {
                background-color: #f9f9f9;
              }
              .roadmap-content hr {
                border: none;
                border-top: 1px solid #ddd;
                margin: 2rem 0;
              }
              .roadmap-content code {
                background-color: #f4f4f4;
                padding: 0.2rem 0.4rem;
                border-radius: 3px;
                font-family: 'Courier New', monospace;
                font-size: 0.9em;
              }
              .roadmap-content pre {
                background-color: #f4f4f4;
                padding: 1rem;
                border-radius: 4px;
                overflow-x: auto;
                margin-bottom: 1rem;
              }
              .roadmap-content pre code {
                background-color: transparent;
                padding: 0;
              }
              .roadmap-content blockquote {
                border-left: 4px solid #2196F3;
                padding-left: 1rem;
                margin-left: 0;
                color: #666;
                font-style: italic;
              }
              .roadmap-content strong {
                font-weight: 600;
                color: #111;
              }
            `}</style>
            <div className="roadmap-content">
              <ReactMarkdown>{markdown}</ReactMarkdown>
            </div>
          </>
        )}
      </div>
    </section>
  )
}
