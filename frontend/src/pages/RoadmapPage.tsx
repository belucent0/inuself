import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/shared/components/ui/tabs'
import { Card, CardContent } from '@/shared/components/ui/card'
import {
  Breadcrumb,
  BreadcrumbList,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbSeparator,
  BreadcrumbPage,
} from '@/shared/components/ui/breadcrumb'
import { Loader2 } from 'lucide-react'
import { cn } from '@/shared/utils/cn'

type TabType = 'completed' | 'developing' | 'considering' | 'longterm'

export default function RoadmapPage() {
  const [activeTab, setActiveTab] = useState<TabType>('completed')
  const [markdownCache, setMarkdownCache] = useState<Record<TabType, string>>({
    completed: '',
    developing: '',
    considering: '',
    longterm: '',
  })
  const [loadingTabs, setLoadingTabs] = useState<Set<TabType>>(new Set<TabType>(['completed']))
  const [error, setError] = useState<string | null>(null)

  const tabFiles: Record<TabType, string> = {
    completed: '/ROADMAP_COMPLETED.md',
    developing: '/ROADMAP_DEVELOPING.md',
    considering: '/ROADMAP_CONSIDERING.md',
    longterm: '/ROADMAP_LONGTERM.md',
  }

  const tabLabels: Record<TabType, string> = {
    completed: '✅ 완료',
    developing: '🔨 개발 중',
    considering: '💭 고려 중',
    longterm: '🎯 장기 도전',
  }

  useEffect(() => {
    const fetchMarkdown = async (tab: TabType) => {
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
  }, [activeTab, markdownCache, tabFiles])

  const renderContent = (tab: TabType) => {
    const isLoading = loadingTabs.has(tab)
    const content = markdownCache[tab]

    if (isLoading) {
      return (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      )
    }

    if (error) {
      return (
        <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-6 text-center">
          <p className="text-destructive">{error}</p>
        </div>
      )
    }

    if (!content) {
      return (
        <div className="rounded-lg border border-border bg-muted/30 p-6 text-center">
          <p className="text-muted-foreground">로드맵 내용이 없습니다.</p>
        </div>
      )
    }

    return (
      <Card>
        <CardContent className="pt-6">
          <div
            className={cn(
              'markdown-content prose dark:prose-invert max-w-none',
              'prose-headings:font-semibold prose-headings:tracking-tight',
              'prose-h1:text-3xl prose-h2:text-2xl prose-h3:text-xl',
              'prose-p:leading-relaxed prose-p:text-foreground/90',
              'prose-a:text-primary prose-a:no-underline hover:prose-a:underline',
              'prose-strong:text-foreground prose-strong:font-semibold',
              'prose-code:text-foreground prose-code:bg-muted prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded',
              'prose-pre:bg-muted prose-pre:border prose-pre:border-border',
              'prose-blockquote:border-l-4 prose-blockquote:border-primary/20 prose-blockquote:bg-muted/30 prose-blockquote:rounded-r-lg',
              'prose-ul:list-disc prose-ol:list-decimal',
              'prose-li:text-foreground/90'
            )}
          >
            <ReactMarkdown
              components={{
                a: ({ ...props }) => (
                  <a
                    {...props}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline font-medium decoration-primary/30 underline-offset-2 transition-colors"
                  />
                ),
                blockquote: ({ ...props }) => (
                  <blockquote
                    {...props}
                    className="border-l-4 border-primary/20 pl-4 py-1 my-4 bg-muted/30 rounded-r-lg italic"
                  />
                ),
              }}
            >
              {content}
            </ReactMarkdown>
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="container mx-auto px-4 py-8 max-w-5xl">
      {/* Page Header with Breadcrumb */}
      <div className="mb-8">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink href="/">홈</BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage>로드맵</BreadcrumbPage>
            </BreadcrumbItem>
          </BreadcrumbList>
        </Breadcrumb>
        <h1 className="text-4xl font-bold tracking-tight mt-4">프로젝트 로드맵</h1>
        <p className="text-muted-foreground mt-2">
          개발 진행 상황과 향후 계획을 확인하세요
        </p>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as TabType)}>
        <TabsList className="grid w-full grid-cols-4 mb-6">
          {(Object.keys(tabLabels) as TabType[]).map((tab) => (
            <TabsTrigger key={tab} value={tab} className="text-sm">
              {tabLabels[tab]}
            </TabsTrigger>
          ))}
        </TabsList>

        {(Object.keys(tabLabels) as TabType[]).map((tab) => (
          <TabsContent key={tab} value={tab}>
            {renderContent(tab)}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
