/**
 * 검사 이력 목록 페이지
 */

import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card"
import { Button } from "@/shared/components/ui/button"
import { Badge } from "@/shared/components/ui/badge"
import { Skeleton } from "@/shared/components/ui/skeleton"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/shared/components/ui/select"
import { useScanHistory, useScanDetail, useWpiAiReport, WpiResultChart } from "@/features/scan"
import { MarkdownContent } from "@/features/chat/components/MarkdownContent"
import type {
  WpiAiReportStatus,
  WpiData,
  WpiITestScores,
  WpiMeTestScores,
} from "@/features/scan"
import { formatToKST } from "@/shared/utils/cn"
import { getScanTypeDisplayName } from "@/shared/config"
import {
  ArrowLeft,
  History,
  ChevronLeft,
  ChevronRight,
  BookOpen,
  Loader2,
  RotateCcw,
} from "lucide-react"

const PAGE_SIZE = 10

const WPI_AI_REPORT_STATUS_LABEL: Record<WpiAiReportStatus, string> = {
  idle: "대기",
  queued: "큐 대기",
  processing: "생성 중",
  completed: "완료",
  failed: "실패",
}

function getAiReportStatusVariant(status: WpiAiReportStatus): "default" | "secondary" | "destructive" {
  if (status === "completed") return "default"
  if (status === "failed") return "destructive"
  return "secondary"
}

export function ScanHistoryPage() {
  const [scanType, setScanType] = useState<string | undefined>(undefined)
  const [status, setStatus] = useState<string | undefined>(undefined)
  const [offset, setOffset] = useState(0)

  const { items, total, loading } = useScanHistory({
    scan_type: scanType,
    status,
    limit: PAGE_SIZE,
    offset,
  })

  const totalPages = Math.ceil(total / PAGE_SIZE)
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  return (
    <div className="container mx-auto py-4 px-2 md:px-4 md:py-6 space-y-4 md:space-y-6">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" asChild>
            <Link to="/scan">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <History className="h-6 w-6" />
            검사 이력
          </h1>
        </div>
      </div>

      {/* 필터 */}
      <Card>
        <CardContent className="p-3 md:pt-4 md:px-6">
          <div className="flex gap-4">
            <Select
              value={scanType || "all"}
              onValueChange={(v) => {
                setScanType(v === "all" ? undefined : v)
                setOffset(0)
              }}
            >
              <SelectTrigger className="w-40">
                <SelectValue placeholder="검사 유형" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">전체</SelectItem>
                <SelectItem value="wpi">WPI</SelectItem>
              </SelectContent>
            </Select>

            <Select
              value={status || "all"}
              onValueChange={(v) => {
                setStatus(v === "all" ? undefined : v)
                setOffset(0)
              }}
            >
              <SelectTrigger className="w-40">
                <SelectValue placeholder="상태" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">전체</SelectItem>
                <SelectItem value="completed">완료</SelectItem>
                <SelectItem value="in_progress">진행중</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      {/* 목록 */}
      <Card>
        <CardContent className="p-3 md:pt-4 md:px-6">
          {loading ? (
            <div className="space-y-3">
              {[1, 2, 3, 4, 5].map((i) => (
                <Skeleton key={i} className="h-20 w-full" />
              ))}
            </div>
          ) : items.length === 0 ? (
            <div className="py-12 text-center">
              <p className="text-muted-foreground">검사 이력이 없습니다</p>
            </div>
          ) : (
            <div className="space-y-3">
              {items.map((item) => (
                <Link
                  key={item.id}
                  to={`/scan/history/${item.id}`}
                  className="block p-3 md:p-4 rounded-lg border hover:bg-accent transition-colors"
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium text-lg">
                          {getScanTypeDisplayName(item.scan_type)}
                        </span>
                        <Badge variant={item.completed ? "default" : "secondary"}>
                          {item.completed ? "완료" : "진행중"}
                        </Badge>
                      </div>
                      {item.summary && item.completed && (
                        <p className="text-sm text-muted-foreground">
                          자기평가: {item.summary.dominant_i_type || "-"} /
                          타인평가: {item.summary.dominant_me_type || "-"}
                        </p>
                      )}
                    </div>
                    <span className="text-sm text-muted-foreground">
                      {formatToKST(item.created_at)}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}

          {/* 페이지네이션 */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-4 mt-6 pt-4 border-t">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                disabled={offset === 0}
              >
                <ChevronLeft className="h-4 w-4" />
                이전
              </Button>
              <span className="text-sm text-muted-foreground">
                {currentPage} / {totalPages} 페이지
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setOffset(offset + PAGE_SIZE)}
                disabled={currentPage >= totalPages}
              >
                다음
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

/**
 * 검사 상세 페이지
 */
export function ScanDetailPage() {
  const { resultId } = useParams<{ resultId: string }>()
  const { detail, loading, error } = useScanDetail(resultId || null)
  const {
    aiReport,
    loading: aiReportLoading,
    generating: aiReportGenerating,
    error: aiReportError,
    enqueueAiReport,
  } = useWpiAiReport(detail?.scan_type === "wpi" ? detail.id : null)

  if (loading) {
    return (
      <div className="container mx-auto py-4 px-2 md:px-4 md:py-6 space-y-4 md:space-y-6 max-w-4xl">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-[400px] w-full" />
      </div>
    )
  }

  if (error || !detail) {
    return (
      <div className="container mx-auto py-4 px-2 md:px-4 md:py-6 space-y-4 md:space-y-6 max-w-4xl">
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-muted-foreground mb-4">
              {error?.message || "검사 결과를 불러올 수 없습니다"}
            </p>
            <Button asChild>
              <Link to="/scan/history">이력으로 돌아가기</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  // WPI 검사인 경우
  if (detail.scan_type === "wpi") {
    const data = detail.data as WpiData
    const aiReportStatus: WpiAiReportStatus = aiReport?.status ?? "idle"
    const isAiReportRunning = aiReportStatus === "queued" || aiReportStatus === "processing"
    const canGenerateAiReport = detail.completed && !isAiReportRunning
    const aiReportButtonLabel =
      aiReportStatus === "completed" ? "마음 읽기 다시 생성" : "마음 읽기 생성"

    return (
      <div className="container mx-auto py-4 px-2 md:px-4 md:py-6 space-y-4 md:space-y-6 max-w-4xl">
        {/* 헤더 */}
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="sm" asChild>
                <Link to="/scan/history">
                  <ArrowLeft className="h-4 w-4" />
                </Link>
              </Button>
              <h1 className="text-xl md:text-2xl font-bold">WPI 검사 결과</h1>
            </div>
            <p className="text-xs md:text-sm text-muted-foreground pl-10">
              검사일: {formatToKST(detail.created_at)}
            </p>
          </div>
          <Badge variant={detail.completed ? "default" : "secondary"}>
            {detail.completed ? "완료" : "진행중"}
          </Badge>
        </div>

        {/* 차트 */}
        {data.i_test && data.me_test ? (
          <WpiResultChart
            iTestScores={data.i_test.scores as WpiITestScores}
            meTestScores={data.me_test.scores as WpiMeTestScores}
            iTestDominant={data.i_test.dominant_type}
            meTestDominant={data.me_test.dominant_type}
          />
        ) : (
          <Card>
            <CardContent className="py-8 space-y-4">
              <div className="text-center text-muted-foreground">
                검사가 완료되지 않아 결과를 표시할 수 없습니다
              </div>
              <div className="flex gap-3 justify-center">
                <Button asChild>
                  <Link to="/scan/wpi">이어하기</Link>
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* 마음 읽기 */}
        <Card>
          <CardHeader className="px-3 md:px-6 py-3 md:py-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div className="flex items-center gap-2">
                <BookOpen className="h-4 w-4 text-indigo-500" />
                <CardTitle className="text-base md:text-lg">마음 읽기</CardTitle>
                <Badge variant={getAiReportStatusVariant(aiReportStatus)}>
                  {WPI_AI_REPORT_STATUS_LABEL[aiReportStatus]}
                </Badge>
              </div>
              <Button
                size="sm"
                onClick={() => void enqueueAiReport(aiReportStatus === "completed")}
                disabled={!canGenerateAiReport || aiReportGenerating}
              >
                {aiReportGenerating ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    요청 중...
                  </>
                ) : aiReportStatus === "completed" ? (
                  <>
                    <RotateCcw className="h-4 w-4 mr-2" />
                    {aiReportButtonLabel}
                  </>
                ) : (
                  aiReportButtonLabel
                )}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="px-3 md:px-6 py-2 md:py-4">
            {!detail.completed ? (
              <p className="text-sm text-muted-foreground">
                검사 완료 후 마음 읽기를 생성할 수 있습니다.
              </p>
            ) : aiReportLoading && !aiReport ? (
              <Skeleton className="h-24 w-full" />
            ) : aiReportError ? (
              <p className="text-sm text-red-500">{aiReportError.message}</p>
            ) : isAiReportRunning ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                마음 읽기 리포트를 생성 중입니다. 잠시만 기다려주세요.
              </div>
            ) : aiReportStatus === "failed" ? (
              <p className="text-sm text-red-500">
                {aiReport?.error || "마음 읽기 생성에 실패했습니다. 다시 시도해주세요."}
              </p>
            ) : aiReportStatus === "completed" && aiReport?.report_md ? (
              <div className="rounded-lg border bg-muted/20 p-3 md:p-4 max-w-3xl leading-relaxed">
                <MarkdownContent content={aiReport.report_md} className="text-sm" />
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                아직 생성된 마음 읽기 리포트가 없습니다. 버튼을 눌러 생성하세요.
              </p>
            )}
          </CardContent>
        </Card>

      </div>
    )
  }

  // 기타 검사 유형 (향후 확장)
  return (
    <div className="container mx-auto py-4 px-2 md:px-4 md:py-6 space-y-4 md:space-y-6 max-w-4xl">
      <Card>
        <CardContent className="py-12 text-center">
          <p className="text-muted-foreground">
            지원하지 않는 검사 유형입니다: {detail.scan_type}
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

export default ScanHistoryPage
