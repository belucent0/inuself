import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FilePenLine, Lightbulb, Loader2, Search, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { Card } from '@/shared/components/ui/card'
import { Label } from '@/shared/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { contentsApi } from '@/shared/services/endpoints/contents'
import { insightPostsApi } from '@/shared/services/endpoints/insightPosts'
import type { ApiError } from '@/shared/services/api/httpClient'

const postTypeOptions = [
  { value: 'insight', label: '인사이트 글' },
  { value: 'review', label: '비판적 리뷰' },
  { value: 'lecture_note', label: '강의 노트' },
  { value: 'curation_note', label: '큐레이션 노트' },
]

const toneOptions = [
  { value: 'analytical', label: '분석적' },
  { value: 'critical', label: '비판적' },
  { value: 'concise', label: '간결한' },
  { value: 'essay', label: '에세이형' },
]

function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
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

function getErrorMessage(error: unknown): string {
  if (!(error instanceof Error)) {
    return '글 생성에 실패했습니다.'
  }

  const apiError = error as ApiError
  const detail =
    apiError.data &&
    typeof apiError.data === 'object' &&
    'detail' in apiError.data
      ? (apiError.data as { detail?: unknown }).detail
      : null

  return typeof detail === 'string' && detail ? detail : error.message
}

export function InsightPostsPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [selectedContentId, setSelectedContentId] = useState('')
  const [postType, setPostType] = useState('insight')
  const [tone, setTone] = useState('analytical')

  const postsQuery = useQuery({
    queryKey: ['insight-posts', 'list'],
    queryFn: () => insightPostsApi.getInsightPosts({ page: 1, pageSize: 24 }),
    staleTime: 30_000,
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => item.status === 'generating') ? 3000 : false,
  })

  const contentsQuery = useQuery({
    queryKey: ['contents', 'insight-source-picker'],
    queryFn: () => contentsApi.getContents({ page: 1, pageSize: 50 }),
    staleTime: 30_000,
  })

  const sourceContents = useMemo(
    () =>
      (contentsQuery.data?.contents ?? []).filter(
        (item) =>
          item.status === 'COMPLETED' &&
          (item.summary_md || item.transcription_content)
      ),
    [contentsQuery.data?.contents]
  )

  const createMutation = useMutation({
    mutationFn: () =>
      insightPostsApi.createInsightPostFromContent(selectedContentId, {
        post_type: postType,
        tone,
        target_length: 'medium',
        include_transcript_quotes: true,
        include_research_prompts: true,
        allow_fallback: false,
      }),
    onSuccess: (post) => {
      queryClient.invalidateQueries({ queryKey: ['insight-posts'] })
      toast.success('인사이트 글 생성 작업을 시작했습니다.')
      navigate(`/insights/${post.id}`)
    },
    onError: (error) => {
      toast.error(getErrorMessage(error))
    },
  })

  const posts = postsQuery.data?.items ?? []

  return (
    <main className="min-h-full bg-muted/20">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 md:px-8">
        <header className="flex flex-col gap-3 border-b pb-5 md:flex-row md:items-end md:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-sm text-muted-foreground">
              <Lightbulb className="h-4 w-4" />
              영상 기반 인사이트 블로그
            </div>
            <h1 className="text-2xl font-semibold tracking-normal">영상 인사이트</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              YouTube 영상과 전사 결과를 바탕으로 읽을 만한 블로그 글을 만들고, 이후 조사 링크와 근거를 덧붙입니다.
            </p>
          </div>
        </header>

        <section className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
          <div className="rounded-lg border bg-background p-4">
            <div className="mb-4 flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              <h2 className="text-base font-semibold">새 글 만들기</h2>
            </div>

            <div className="space-y-4">
              <div className="space-y-2">
                <Label>원본 콘텐츠</Label>
                <Select value={selectedContentId} onValueChange={setSelectedContentId}>
                  <SelectTrigger>
                    <SelectValue placeholder="전사 완료된 콘텐츠 선택" />
                  </SelectTrigger>
                  <SelectContent>
                    {sourceContents.map((content) => (
                      <SelectItem key={content.id} value={content.id}>
                        {content.title || content.filename}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label>글 타입</Label>
                  <Select value={postType} onValueChange={setPostType}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {postTypeOptions.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>톤</Label>
                  <Select value={tone} onValueChange={setTone}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {toneOptions.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <Button
                className="w-full gap-2"
                disabled={!selectedContentId || createMutation.isPending}
                onClick={() => createMutation.mutate()}
              >
                {createMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <FilePenLine className="h-4 w-4" />
                )}
                인사이트 글 생성
              </Button>
            </div>
          </div>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold">작성 중인 글</h2>
              <Badge variant="outline">{posts.length}개</Badge>
            </div>

            {postsQuery.isLoading ? (
              <div className="flex h-40 items-center justify-center rounded-lg border bg-background">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : posts.length === 0 ? (
              <div className="flex h-56 flex-col items-center justify-center rounded-lg border bg-background text-center">
                <Search className="mb-3 h-8 w-8 text-muted-foreground" />
                <p className="text-sm font-medium">아직 생성된 인사이트 글이 없습니다.</p>
                <p className="mt-1 text-sm text-muted-foreground">전사 완료된 콘텐츠를 선택해 첫 글을 만들어보세요.</p>
              </div>
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {posts.map((post) => (
                  <Link key={post.id} to={`/insights/${post.id}`}>
                    <Card className="h-full p-4 transition-shadow hover:shadow-md">
                      <div className="mb-3 flex items-center justify-between gap-3">
                        <Badge variant={getPostStatusVariant(post.status)}>
                          {getPostStatusLabel(post.status)}
                        </Badge>
                        <span className="text-xs text-muted-foreground">{formatDate(post.created_at)}</span>
                      </div>
                      <h3 className="line-clamp-2 text-base font-semibold leading-snug">{post.title}</h3>
                      {post.subtitle && (
                        <p className="mt-2 line-clamp-2 text-sm leading-6 text-muted-foreground">{post.subtitle}</p>
                      )}
                      <div className="mt-4 flex items-center justify-between gap-3 border-t pt-3 text-xs text-muted-foreground">
                        <span className="line-clamp-1">원본: {post.source_title}</span>
                        <span>{post.evidence_count} 근거</span>
                      </div>
                    </Card>
                  </Link>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  )
}
