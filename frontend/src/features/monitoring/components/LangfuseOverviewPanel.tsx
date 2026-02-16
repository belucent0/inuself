import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Brain, Clock3, DollarSign, ExternalLink, Loader2, ShieldAlert, Star, Workflow } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/components/ui/card'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from '@/shared/components/ui/chart'
import { Line, LineChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import { apiService } from '@/shared/services'
import type { LangfuseOverview, LangfuseTraceDetailResponse, LangfuseTraceItem } from '@/shared/services/endpoints/langfuse'

const trendConfig = {
  request_count: {
    label: '요청 수',
    color: 'hsl(var(--chart-1))',
  },
  avg_latency_ms: {
    label: '평균 지연(ms)',
    color: 'hsl(var(--chart-2))',
  },
} satisfies ChartConfig

function formatCost(value: number): string {
  if (value >= 1) {
    return `$${value.toFixed(2)}`
  }
  return `$${value.toFixed(4)}`
}

function formatMode(mode: string | null): string {
  if (!mode) {
    return '모드 미상'
  }

  const normalized = mode.toLowerCase()
  const labels: Record<string, string> = {
    auto: '자동',
    simple: '일반',
    search: '웹검색',
    rag: '문서검색',
    hybrid: '하이브리드',
    reasoning: '추론',
  }

  return labels[normalized] ?? mode
}

function formatStatus(status: string): { label: string; isError: boolean } {
  const normalized = status.toLowerCase()
  if (normalized === 'error' || normalized === 'failed') {
    return { label: '오류', isError: true }
  }
  if (normalized === 'running' || normalized === 'processing' || normalized === 'pending') {
    return { label: '진행중', isError: false }
  }
  return { label: '완료', isError: false }
}

function shortId(value: string | null): string {
  if (!value) {
    return '-'
  }
  if (value.length <= 16) {
    return value
  }
  return `${value.slice(0, 8)}...${value.slice(-4)}`
}

function formatCreatedAt(value: string | number | null): string {
  if (value === null || value === undefined) {
    return '-'
  }

  const date =
    typeof value === 'number'
      ? new Date(value * 1000)
      : new Date(value)

  if (Number.isNaN(date.getTime())) {
    return '-'
  }

  return date.toLocaleString('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatObservationType(type: string | null): string {
  if (!type) {
    return '기타'
  }

  const normalized = type.toLowerCase()
  const labels: Record<string, string> = {
    generation: 'LLM 생성',
    span: '스팬',
    event: '이벤트',
  }

  return labels[normalized] ?? type
}

export function LangfuseOverviewPanel() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [overview, setOverview] = useState<LangfuseOverview | null>(null)
  const [traces, setTraces] = useState<LangfuseTraceItem[]>([])
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null)
  const [traceDetail, setTraceDetail] = useState<LangfuseTraceDetailResponse | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true

    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const [overviewData, tracesData] = await Promise.all([
          apiService.langfuse.getLangfuseOverview(24, 100),
          apiService.langfuse.getLangfuseTraces(8),
        ])
        if (!alive) {
          return
        }
        setOverview(overviewData)
        setTraces(tracesData.traces)
      } catch (err) {
        if (!alive) {
          return
        }
        setError(err instanceof Error ? err.message : 'Langfuse 데이터를 가져오지 못했습니다.')
      } finally {
        if (alive) {
          setLoading(false)
        }
      }
    }

    load()
    return () => {
      alive = false
    }
  }, [])

  const warningMessages = useMemo(() => {
    if (!overview) {
      return []
    }
    return overview.errors.filter((item) => item.trim().length > 0)
  }, [overview])

  const selectedTrace = useMemo(() => {
    if (!selectedTraceId) {
      return null
    }
    return traces.find((trace) => trace.trace_id === selectedTraceId) ?? null
  }, [selectedTraceId, traces])

  const detailWarnings = useMemo(() => {
    if (!traceDetail) {
      return []
    }
    return traceDetail.errors.filter((item) => item.trim().length > 0)
  }, [traceDetail])

  const loadTraceDetail = async (trace: LangfuseTraceItem) => {
    setSelectedTraceId(trace.trace_id)
    setDetailLoading(true)
    setDetailError(null)

    try {
      const detailData = await apiService.langfuse.getLangfuseTraceDetail(trace.trace_id)
      setTraceDetail(detailData)

      const sessionId = trace.session_id ?? trace.thread_id
      if (sessionId && !detailData.session) {
        const sessionData = await apiService.langfuse.getLangfuseSessionTimeline(sessionId, 50)
        setTraceDetail({
          ...detailData,
          session: {
            session_id: sessionData.session_id,
            trace_count: sessionData.traces.length,
            traces: sessionData.traces,
          },
          errors: [...detailData.errors, ...sessionData.errors],
        })
      }
    } catch (err) {
      setTraceDetail(null)
      setDetailError(err instanceof Error ? err.message : 'Trace 상세를 가져오지 못했습니다.')
    } finally {
      setDetailLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Langfuse 데이터 불러오는 중...
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-destructive">
              <ShieldAlert className="h-4 w-4" />
              Langfuse API 오류
            </CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
        </Card>
      </div>
    )
  }

  if (!overview || !overview.configured) {
    return (
      <div className="p-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Brain className="h-4 w-4" />
              Langfuse 설정 필요
            </CardTitle>
            <CardDescription>
              LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY 환경 변수를 확인해 주세요.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-4 overflow-auto h-full">
      {warningMessages.length > 0 && (
        <Card className="border-amber-300 bg-amber-50/50">
          <CardHeader className="py-4">
            <CardTitle className="text-sm flex items-center gap-2 text-amber-700">
              <AlertTriangle className="h-4 w-4" />
              일부 데이터가 제한적으로 조회되었습니다
            </CardTitle>
            <CardDescription className="space-y-1">
              {warningMessages.map((msg) => (
                <p key={msg}>{msg}</p>
              ))}
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>24h Trace 수</CardDescription>
            <CardTitle className="text-2xl flex items-center gap-2">
              <Workflow className="h-5 w-5 text-primary" />
              {overview.summary.trace_count.toLocaleString()}
            </CardTitle>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>오류 Trace</CardDescription>
            <CardTitle className="text-2xl flex items-center gap-2">
              <ShieldAlert className="h-5 w-5 text-destructive" />
              {overview.summary.error_count.toLocaleString()}
            </CardTitle>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>평균 지연</CardDescription>
            <CardTitle className="text-2xl flex items-center gap-2">
              <Clock3 className="h-5 w-5 text-sky-600" />
              {overview.summary.avg_latency_ms.toFixed(1)}ms
            </CardTitle>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>총 비용(24h)</CardDescription>
            <CardTitle className="text-2xl flex items-center gap-2">
              <DollarSign className="h-5 w-5 text-emerald-600" />
              {formatCost(overview.summary.total_cost_usd)}
            </CardTitle>
          </CardHeader>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>평균 점수</CardDescription>
            <CardTitle className="text-2xl flex items-center gap-2">
              <Star className="h-5 w-5 text-amber-500" />
              {overview.summary.avg_score.toFixed(3)}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>시간대별 요청 수</CardTitle>
            <CardDescription>최근 24시간 Langfuse trace 집계</CardDescription>
          </CardHeader>
          <CardContent>
            <ChartContainer config={trendConfig} className="h-[280px] w-full">
              <LineChart data={overview.trend} margin={{ top: 8, left: 4, right: 12, bottom: 8 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="bucket" tickLine={false} axisLine={false} minTickGap={24} />
                <YAxis yAxisId="left" tickLine={false} axisLine={false} allowDecimals={false} />
                <YAxis yAxisId="right" orientation="right" tickLine={false} axisLine={false} />
                <ChartTooltip content={<ChartTooltipContent />} />
                <Line
                  yAxisId="left"
                  dataKey="request_count"
                  type="monotone"
                  stroke="var(--color-request_count)"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  yAxisId="right"
                  dataKey="avg_latency_ms"
                  type="monotone"
                  stroke="var(--color-avg_latency_ms)"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ChartContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>최근 Trace</CardTitle>
            <CardDescription>{overview.host} · 대화/턴 기준 요약</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {traces.length === 0 ? (
              <div className="text-sm text-muted-foreground">표시할 trace가 없습니다.</div>
            ) : (
              traces.map((trace) => {
                const status = formatStatus(trace.status)
                const isSelected = selectedTraceId === trace.trace_id

                return (
                  <div
                    key={`${trace.trace_id}-${trace.created_at}`}
                    className={`p-3 border rounded-md space-y-2 ${isSelected ? 'ring-2 ring-primary/60 border-primary/40' : ''}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 space-y-1">
                        <div className="font-medium truncate">{trace.display_name || trace.name}</div>
                        <div className="text-xs text-muted-foreground truncate">
                          {trace.query_preview ?? trace.trace_id}
                        </div>
                      </div>

                      <div className="flex items-center gap-2 text-xs shrink-0">
                        <Badge variant={status.isError ? 'destructive' : 'outline'}>{status.label}</Badge>
                        <span>{trace.latency_ms.toFixed(1)}ms</span>
                        <span>{formatCost(trace.cost_usd)}</span>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                      <Badge variant="outline">{formatMode(trace.mode)}</Badge>
                      <Badge variant="outline">턴 {trace.turn_index ?? '-'}</Badge>
                      <span>thread {shortId(trace.thread_id)}</span>
                      <span>msg {shortId(trace.message_id)}</span>
                      <span>{formatCreatedAt(trace.created_at)}</span>
                      <Button
                        type="button"
                        size="sm"
                        variant={isSelected ? 'secondary' : 'outline'}
                        className="h-6 px-2 text-[11px]"
                        onClick={() => {
                          void loadTraceDetail(trace)
                        }}
                      >
                        앱 상세
                      </Button>
                      {trace.trace_path && (
                        <a
                          href={trace.trace_path}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-primary hover:text-primary/80"
                        >
                          상세
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                    </div>
                  </div>
                )
              })
            )}
          </CardContent>
        </Card>
      </div>

      {selectedTrace && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              선택 Trace 상세
              <Badge variant="outline">{selectedTrace.display_name || selectedTrace.name}</Badge>
            </CardTitle>
            <CardDescription>
              trace {selectedTrace.trace_id} · session {shortId(selectedTrace.session_id ?? selectedTrace.thread_id)}
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4">
            {detailLoading ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                상세 데이터를 불러오는 중...
              </div>
            ) : detailError ? (
              <div className="text-sm text-destructive">{detailError}</div>
            ) : traceDetail?.trace ? (
              <>
                {detailWarnings.length > 0 && (
                  <div className="rounded-md border border-amber-300 bg-amber-50/50 p-3 text-xs text-amber-800 space-y-1">
                    {detailWarnings.map((message) => (
                      <p key={message}>{message}</p>
                    ))}
                  </div>
                )}

                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-md border p-3 space-y-1">
                    <div className="text-xs text-muted-foreground">사용자 입력</div>
                    <div className="text-sm leading-relaxed break-words">
                      {traceDetail.trace.input_preview ?? selectedTrace.query_preview ?? '-'}
                    </div>
                  </div>
                  <div className="rounded-md border p-3 space-y-1">
                    <div className="text-xs text-muted-foreground">모델 출력</div>
                    <div className="text-sm leading-relaxed break-words">
                      {traceDetail.trace.output_preview ?? '-'}
                    </div>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline">{formatStatus(traceDetail.trace.status).label}</Badge>
                  <Badge variant="outline">{formatMode(traceDetail.trace.mode)}</Badge>
                  <Badge variant="outline">턴 {traceDetail.trace.turn_index ?? '-'}</Badge>
                  <span>thread {shortId(traceDetail.trace.thread_id)}</span>
                  <span>msg {shortId(traceDetail.trace.message_id)}</span>
                  <span>{formatCreatedAt(traceDetail.trace.created_at)}</span>
                  {traceDetail.trace.trace_path && (
                    <a
                      href={traceDetail.trace.trace_path}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:text-primary/80"
                    >
                      Langfuse 상세
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>

                {traceDetail.session && traceDetail.session.traces.length > 1 && (
                  <div className="space-y-2">
                    <div className="text-sm font-medium">세션 타임라인 ({traceDetail.session.trace_count})</div>
                    <div className="grid gap-2 max-h-52 overflow-auto pr-1">
                      {traceDetail.session.traces.map((item) => {
                        const sameTrace = item.trace_id === traceDetail.trace?.trace_id
                        return (
                          <button
                            key={`${item.trace_id}-${item.created_at}`}
                            type="button"
                            className={`text-left rounded-md border p-2 text-xs space-y-1 hover:border-primary/60 ${sameTrace ? 'border-primary bg-primary/5' : ''}`}
                            onClick={() => {
                              void loadTraceDetail(item)
                            }}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-medium truncate">{item.display_name || item.name}</span>
                              <Badge variant="outline">턴 {item.turn_index ?? '-'}</Badge>
                            </div>
                            <div className="text-muted-foreground truncate">{item.query_preview ?? item.trace_id}</div>
                            <div className="text-muted-foreground">{formatCreatedAt(item.created_at)}</div>
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )}

                <div className="space-y-2">
                  <div className="text-sm font-medium">Observations ({traceDetail.observations.length})</div>
                  {traceDetail.observations.length === 0 ? (
                    <div className="text-xs text-muted-foreground">관찰(Span/Generation/Event) 데이터가 없습니다.</div>
                  ) : (
                    <div className="grid gap-2 max-h-80 overflow-auto pr-1">
                      {traceDetail.observations.map((observation) => (
                        <div key={observation.observation_id} className="rounded-md border p-3 space-y-1">
                          <div className="flex flex-wrap items-center gap-2 text-xs">
                            <span className="font-medium text-sm">{observation.name || '(unnamed)'}</span>
                            <Badge variant="outline">{formatObservationType(observation.type)}</Badge>
                            <Badge variant={observation.status === 'error' ? 'destructive' : 'outline'}>
                              {observation.status === 'error' ? '오류' : '완료'}
                            </Badge>
                            {observation.model && <Badge variant="outline">{observation.model}</Badge>}
                          </div>
                          <div className="text-xs text-muted-foreground flex flex-wrap gap-x-3 gap-y-1">
                            <span>latency {observation.latency_ms.toFixed(1)}ms</span>
                            <span>cost {formatCost(observation.cost_usd)}</span>
                            <span>{formatCreatedAt(observation.start_time)}</span>
                          </div>
                          {observation.status_message && (
                            <div className="text-xs text-destructive">{observation.status_message}</div>
                          )}
                          {(observation.input_preview || observation.output_preview) && (
                            <div className="grid gap-1 md:grid-cols-2 text-xs">
                              <div className="rounded-sm bg-muted/40 p-2 break-words">
                                <div className="text-[11px] text-muted-foreground mb-1">입력</div>
                                {observation.input_preview ?? '-'}
                              </div>
                              <div className="rounded-sm bg-muted/40 p-2 break-words">
                                <div className="text-[11px] text-muted-foreground mb-1">출력</div>
                                {observation.output_preview ?? '-'}
                              </div>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="text-sm text-muted-foreground">상세 데이터가 없습니다.</div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
