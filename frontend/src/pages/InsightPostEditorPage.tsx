import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  BookOpenText,
  ExternalLink,
  FileText,
  Loader2,
  PanelRightOpen,
  RefreshCcw,
  Save,
  Search,
} from 'lucide-react'
import { toast } from 'sonner'
import { MarkdownContent } from '@/features/chat/components/MarkdownContent'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/shared/components/ui/sheet'
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/shared/components/ui/tabs'
import { Textarea } from '@/shared/components/ui/textarea'
import { insightPostsApi } from '@/shared/services/endpoints/insightPosts'
import type { InsightPostDetail } from '@/features/insight'

function formatTimestamp(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return ''
  const total = Math.floor(seconds)
  const hrs = Math.floor(total / 3600)
  const mins = Math.floor((total % 3600) / 60)
  const secs = total % 60
  return `${String(hrs).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}

function postHasUnsavedChanges(post: InsightPostDetail | undefined, title: string, subtitle: string, body: string) {
  if (!post) return false
  return post.title !== title || (post.subtitle ?? '') !== subtitle || post.body_md !== body
}

function getPostStatusLabel(status: string): string {
  if (status === 'generating') return '생성 중'
  if (status === 'failed') return '실패'
  if (status === 'draft') return '초안'
  if (status === 'published') return '게시됨'
  return status
}

function getPostStatusVariant(status: string): 'secondary' | 'warning' | 'destructive' | 'success' | 'outline' {
  if (status === 'generating') return 'warning'
  if (status === 'failed') return 'destructive'
  if (status === 'published') return 'success'
  if (status === 'draft') return 'secondary'
  return 'outline'
}

export function InsightPostEditorPage() {
  const { id } = useParams()
  const queryClient = useQueryClient()
  const [titleDraft, setTitleDraft] = useState<string | null>(null)
  const [subtitleDraft, setSubtitleDraft] = useState<string | null>(null)
  const [bodyDraft, setBodyDraft] = useState<string | null>(null)
  const [sourceOpen, setSourceOpen] = useState(false)
  const [evidenceOpen, setEvidenceOpen] = useState(false)
  const [researchQuery, setResearchQuery] = useState('')

  const postQuery = useQuery({
    queryKey: ['insight-posts', 'detail', id],
    queryFn: () => insightPostsApi.getInsightPost(id!),
    enabled: !!id,
    staleTime: 15_000,
    refetchInterval: (query) =>
      query.state.data?.status === 'generating' ? 3000 : false,
  })

  const saveMutation = useMutation({
    mutationFn: () =>
      insightPostsApi.updateInsightPost(id!, {
        title: titleDraft ?? postQuery.data?.title ?? '',
        subtitle: subtitleDraft ?? postQuery.data?.subtitle ?? '',
        body_md: bodyDraft ?? postQuery.data?.body_md ?? '',
      }),
    onSuccess: (post) => {
      queryClient.setQueryData(['insight-posts', 'detail', id], post)
      queryClient.invalidateQueries({ queryKey: ['insight-posts', 'list'] })
      setTitleDraft(null)
      setSubtitleDraft(null)
      setBodyDraft(null)
      toast.success('저장했습니다.')
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : '저장에 실패했습니다.')
    },
  })

  const researchMutation = useMutation({
    mutationFn: () =>
      insightPostsApi.researchInsightPost(id!, {
        query: researchQuery.trim() || null,
        max_results: 5,
        append_to_body: true,
      }),
    onSuccess: (post) => {
      const previousEvidenceCount = postQuery.data?.evidences.length ?? 0
      const addedCount = Math.max(0, post.evidences.length - previousEvidenceCount)
      queryClient.setQueryData(['insight-posts', 'detail', id], post)
      setTitleDraft(null)
      setSubtitleDraft(null)
      setBodyDraft(null)
      setEvidenceOpen(true)
      if (addedCount > 0) {
        toast.success(`조사 링크 ${addedCount}개를 추가했습니다.`)
      } else {
        toast.info('새로 추가할 조사 링크가 없습니다.')
      }
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : '조사에 실패했습니다.')
    },
  })

  const regenerateMutation = useMutation({
    mutationFn: () => {
      const currentPost = postQuery.data
      if (!currentPost) throw new Error('인사이트 글을 찾을 수 없습니다.')
      const targetLength =
        typeof currentPost.metadata?.target_length === 'string'
          ? currentPost.metadata.target_length
          : 'medium'

      return insightPostsApi.regenerateInsightPost(id!, {
        post_type: currentPost.post_type || 'insight',
        tone: currentPost.tone || 'analytical',
        target_length: targetLength,
        include_transcript_quotes: true,
        include_research_prompts: true,
        allow_fallback: false,
      })
    },
    onSuccess: (post) => {
      queryClient.setQueryData(['insight-posts', 'detail', id], post)
      queryClient.invalidateQueries({ queryKey: ['insight-posts', 'list'] })
      setTitleDraft(null)
      setSubtitleDraft(null)
      setBodyDraft(null)
      toast.success('글을 다시 생성하기 시작했습니다.')
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : '다시 생성에 실패했습니다.')
    },
  })

  const post = postQuery.data
  const title = titleDraft ?? post?.title ?? ''
  const subtitle = subtitleDraft ?? post?.subtitle ?? ''
  const body = bodyDraft ?? post?.body_md ?? ''
  const unsaved = postHasUnsavedChanges(post, title, subtitle, body)
  const evidenceCount = post?.evidences.length ?? 0
  const sortedEvidences = useMemo(
    () =>
      [...(post?.evidences ?? [])].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      ),
    [post?.evidences]
  )
  const isGenerating = post?.status === 'generating'
  const isFailed = post?.status === 'failed'
  const generationMode =
    typeof post?.metadata?.generation_mode === 'string' ? post.metadata.generation_mode : null

  const researchHint = useMemo(() => {
    const queries = post?.metadata?.research_queries
    return Array.isArray(queries) && typeof queries[0] === 'string' ? queries[0] : post?.title ?? ''
  }, [post?.metadata, post?.title])

  if (postQuery.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!post) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        인사이트 글을 찾을 수 없습니다.
      </div>
    )
  }

  return (
    <main className="min-h-full bg-[#f7f8fa]">
      <div className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center gap-2 px-4 py-3 md:px-8">
          <Button variant="ghost" size="icon" asChild>
            <Link to="/insights">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <BookOpenText className="h-3.5 w-3.5" />
              <span>영상 인사이트</span>
              {unsaved && <span>저장되지 않은 변경</span>}
            </div>
            <h1 className="truncate text-sm font-medium">{title || post.title}</h1>
          </div>
          <Button variant="outline" size="sm" className="gap-2" onClick={() => setSourceOpen(true)}>
            <FileText className="h-4 w-4" />
            원본
          </Button>
          <Button variant="outline" size="sm" className="gap-2" onClick={() => setEvidenceOpen(true)}>
            <PanelRightOpen className="h-4 w-4" />
            근거 {evidenceCount > 0 && <Badge variant="secondary">{evidenceCount}</Badge>}
          </Button>
          {isFailed && (
            <Button
              variant="outline"
              size="sm"
              className="gap-2"
              disabled={regenerateMutation.isPending}
              onClick={() => regenerateMutation.mutate()}
            >
              {regenerateMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCcw className="h-4 w-4" />
              )}
              다시 생성
            </Button>
          )}
          <Button
            size="sm"
            className="gap-2"
            disabled={isGenerating || !unsaved || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            저장
          </Button>
        </div>
      </div>

      <div className="mx-auto max-w-4xl px-4 py-8 md:px-8">
        {isGenerating && (
          <div className="mb-4 flex items-start gap-3 rounded-md border border-orange-200 bg-orange-50 p-4 text-sm text-orange-900">
            <Loader2 className="mt-0.5 h-4 w-4 animate-spin" />
            <div>
              <p className="font-medium">글을 생성 중입니다.</p>
              <p className="mt-1 text-orange-800">
                이 화면은 자동으로 갱신됩니다. 원본과 근거는 생성 중에도 확인할 수 있습니다.
              </p>
            </div>
          </div>
        )}
        {isFailed && (
          <div className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            <p className="font-medium">글 생성에 실패했습니다.</p>
            <p className="mt-1">
              AI Gateway 상태나 원본 콘텐츠를 확인한 뒤 다시 생성해 주세요.
            </p>
          </div>
        )}
        <article className="rounded-lg border bg-background px-5 py-6 shadow-sm md:px-12 md:py-10">
          <div className="mx-auto max-w-2xl">
            <div className="mb-8 space-y-4">
              <Input
                value={title}
                onChange={(event) => setTitleDraft(event.target.value)}
                disabled={isGenerating}
                className="h-auto border-0 px-0 text-3xl font-semibold leading-tight shadow-none focus-visible:ring-0 md:text-4xl"
                placeholder="제목"
              />
              <Textarea
                value={subtitle}
                onChange={(event) => setSubtitleDraft(event.target.value)}
                disabled={isGenerating}
                className="min-h-0 resize-none border-0 px-0 text-lg leading-7 text-muted-foreground shadow-none focus-visible:ring-0"
                placeholder="부제 또는 한 줄 요약"
                rows={2}
              />
              <div className="flex flex-wrap items-center gap-2 border-y py-3 text-xs text-muted-foreground">
                <Badge variant={getPostStatusVariant(post.status)}>
                  {getPostStatusLabel(post.status)}
                </Badge>
                <Badge variant="outline">{post.post_type}</Badge>
                <Badge variant="outline">{post.tone}</Badge>
                {generationMode && (
                  <Badge variant={generationMode === 'fallback' ? 'destructive' : 'secondary'}>
                    {generationMode === 'llm' ? 'AI 생성' : 'Fallback'}
                  </Badge>
                )}
                <span>원본: {post.source?.title ?? post.source_title}</span>
              </div>
            </div>

            <Tabs defaultValue="preview" className="space-y-5">
              <TabsList>
                <TabsTrigger value="preview">미리보기</TabsTrigger>
                <TabsTrigger value="edit">편집</TabsTrigger>
              </TabsList>
              <TabsContent value="preview" className="mt-0">
                <MarkdownContent content={body} />
              </TabsContent>
              <TabsContent value="edit" className="mt-0">
                <Textarea
                  value={body}
                  onChange={(event) => setBodyDraft(event.target.value)}
                  disabled={isGenerating}
                  className="min-h-[620px] resize-y border-muted bg-muted/20 font-mono text-sm leading-6"
                />
              </TabsContent>
            </Tabs>
          </div>
        </article>
      </div>

      <Sheet open={sourceOpen} onOpenChange={setSourceOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>원본과 전사</SheetTitle>
            <SheetDescription>
              글 생성에 사용된 영상 요약과 전사 원문입니다.
            </SheetDescription>
          </SheetHeader>
          <div className="mt-6 space-y-5">
            {post.source?.media_url && (
              <video src={post.source.media_url} controls className="w-full rounded-md bg-black" />
            )}
            {post.source?.source_url && (
              <Button variant="outline" size="sm" className="gap-2" asChild>
                <a href={post.source.source_url} target="_blank" rel="noreferrer">
                  YouTube 원본 열기
                  <ExternalLink className="h-4 w-4" />
                </a>
              </Button>
            )}
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">요약</h3>
              <div className="rounded-md border bg-muted/20 p-3 text-sm leading-6">
                {post.source?.summary_md ? (
                  <MarkdownContent content={post.source.summary_md} />
                ) : (
                  <p className="text-muted-foreground">요약이 없습니다.</p>
                )}
              </div>
            </section>
            <section className="space-y-2">
              <h3 className="text-sm font-semibold">전사</h3>
              <pre className="max-h-[520px] overflow-y-auto whitespace-pre-wrap rounded-md border bg-muted/20 p-3 text-sm leading-6">
                {post.source?.transcript_text || '전사 내용이 없습니다.'}
              </pre>
            </section>
          </div>
        </SheetContent>
      </Sheet>

      <Sheet open={evidenceOpen} onOpenChange={setEvidenceOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>근거와 추가 조사</SheetTitle>
            <SheetDescription>
              영상 인용과 웹 검색으로 수집한 링크를 관리합니다.
            </SheetDescription>
          </SheetHeader>

          <div className="mt-6 space-y-5">
            <div className="rounded-md border bg-muted/20 p-3">
              <Label htmlFor="research-query">추가 조사 검색어</Label>
              <div className="mt-2 flex gap-2">
                <Input
                  id="research-query"
                  value={researchQuery}
                  onChange={(event) => setResearchQuery(event.target.value)}
                  placeholder={researchHint || '조사할 주제를 입력하세요'}
                />
                <Button
                  className="gap-2"
                  disabled={isGenerating || isFailed || researchMutation.isPending}
                  onClick={() => researchMutation.mutate()}
                >
                  {researchMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Search className="h-4 w-4" />
                  )}
                  조사
                </Button>
              </div>
            </div>

            <div className="space-y-3">
              {sortedEvidences.length === 0 ? (
                <p className="rounded-md border p-4 text-sm text-muted-foreground">
                  아직 저장된 근거가 없습니다. 조사 버튼을 눌러 관련 링크를 추가할 수 있습니다.
                </p>
              ) : (
                sortedEvidences.map((evidence) => (
                  <div key={evidence.id} className="rounded-md border p-3">
                    <div className="mb-2 flex items-center gap-2">
                      <Badge variant={evidence.source_type === 'web' ? 'secondary' : 'outline'}>
                        {evidence.source_type}
                      </Badge>
                      {evidence.timestamp_seconds !== null && evidence.timestamp_seconds !== undefined && (
                        <span className="font-mono text-xs text-muted-foreground">
                          {formatTimestamp(evidence.timestamp_seconds)}
                        </span>
                      )}
                    </div>
                    <h3 className="text-sm font-semibold leading-5">{evidence.title}</h3>
                    {evidence.quote_text && (
                      <blockquote className="mt-2 border-l-2 pl-3 text-sm leading-6 text-muted-foreground">
                        {evidence.quote_text}
                      </blockquote>
                    )}
                    {evidence.snippet && (
                      <p className="mt-2 text-sm leading-6 text-muted-foreground">{evidence.snippet}</p>
                    )}
                    {evidence.url && (
                      <a
                        href={evidence.url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
                      >
                        원문 열기
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </main>
  )
}
