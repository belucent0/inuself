'use client'

import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'

type TabType = 'developing' | 'considering' | 'longterm'

export default function RoadmapPage() {
  const [activeTab, setActiveTab] = useState<TabType>('developing')
  const [markdownCache, setMarkdownCache] = useState<Record<TabType, string>>({
    developing: '',
    considering: '',
    longterm: '',
  })
  const [loadingTabs, setLoadingTabs] = useState<Set<TabType>>(new Set<TabType>(['developing']))
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
    const fetchMarkdown = async (tab: TabType) => {
      // 이미 로드된 탭은 다시 로드하지 않음
      if (markdownCache[tab]) {
        return
      }

      setLoadingTabs((prev) => new Set(prev).add(tab))
      setError(null)
      try {
        const response = await fetch(tabFiles[tab])
        if (!response.ok) {
          throw new Error('로드맵 파일을 불러올 수 없습니다.')
        }
        const text = await response.text()
        setMarkdownCache((prev) => ({ ...prev, [tab]: text }))
      } catch (err) {
        setError(err instanceof Error ? err.message : '로드맵을 불러오는데 실패했습니다.')
      } finally {
        setLoadingTabs((prev) => {
          const next = new Set(prev)
          next.delete(tab)
          return next
        })
      }
    }

    fetchMarkdown(activeTab)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab])

  return (
    <section>
      <h2>로드맵</h2>
      <div className="card">
        <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as TabType)} className="w-full">
          <TabsList className="grid w-full grid-cols-3 mb-6 h-14 md:h-10 p-1.5 md:p-1">
            {(['developing', 'considering', 'longterm'] as TabType[]).map((tab) => (
              <TabsTrigger 
                key={tab} 
                value={tab}
                className="flex-1 min-w-0 py-2 md:py-1.5"
              >
                <span className="truncate">{tabLabels[tab]}</span>
              </TabsTrigger>
            ))}
          </TabsList>

          {(['developing', 'considering', 'longterm'] as TabType[]).map((tab) => (
            <TabsContent key={tab} value={tab} className="mt-0">
              {loadingTabs.has(tab) ? (
                <p>로딩 중...</p>
              ) : error && !markdownCache[tab] ? (
                <p className="text-red-600">{error}</p>
              ) : markdownCache[tab] ? (
                <div className="markdown-content">
                  <ReactMarkdown>{markdownCache[tab]}</ReactMarkdown>
                </div>
              ) : null}
            </TabsContent>
          ))}
        </Tabs>
      </div>
    </section>
  )
}
