/**
 * WPI 검사 결과 페이지
 * - 프로파일 차트
 * - 마음 읽기 리포트
 */

import { Link } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card"
import { Button } from "@/shared/components/ui/button"
import { Badge } from "@/shared/components/ui/badge"
import { Skeleton } from "@/shared/components/ui/skeleton"
import { useWpiProfile, useWpiAiReport, WpiResultChart } from "@/features/scan"
import type { WpiData, WpiITestScores, WpiMeTestScores, WpiAiReportStatus } from "@/features/scan"
import { MarkdownContent } from "@/features/chat/components/MarkdownContent"
import { formatToKST } from "@/shared/utils/cn"
import { ArrowLeft, BookOpen, Loader2, RotateCcw } from "lucide-react"

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

export function WpiResultPage() {
  const { profile, loading, error } = useWpiProfile()
  const {
    aiReport,
    loading: aiReportLoading,
    generating: aiReportGenerating,
    error: aiReportError,
    enqueueAiReport,
  } = useWpiAiReport(profile?.id ?? null)

  if (loading) {
    return (
      <div className="container mx-auto py-6 space-y-6 max-w-4xl">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-[400px] w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  if (error || !profile) {
    return (
      <div className="container mx-auto py-6 space-y-6 max-w-4xl">
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-muted-foreground mb-4">
              {error?.message || "검사 결과를 불러올 수 없습니다"}
            </p>
            <Button asChild>
              <Link to="/scan">검사 페이지로 돌아가기</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  const data = profile.data as WpiData

  if (!data.i_test || !data.me_test) {
    return (
      <div className="container mx-auto py-6 space-y-6 max-w-4xl">
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-muted-foreground mb-4">
              검사가 아직 완료되지 않았습니다
            </p>
            <Button asChild>
              <Link to="/scan/wpi">검사 이어하기</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="container mx-auto py-6 space-y-6 max-w-4xl">
      {/* 헤더 */}
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" asChild>
              <Link to="/scan">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <h1 className="text-2xl font-bold">WPI 검사 결과</h1>
          </div>
          <p className="text-sm text-muted-foreground pl-10">
            검사일: {formatToKST(profile.created_at)}
          </p>
        </div>
        <Badge variant={profile.completed ? "default" : "secondary"}>
          {profile.completed ? "완료" : "진행중"}
        </Badge>
      </div>

      {/* 프로파일 차트 */}
      <WpiResultChart
        iTestScores={data.i_test.scores as WpiITestScores}
        meTestScores={data.me_test.scores as WpiMeTestScores}
        iTestDominant={data.i_test.dominant_type}
        meTestDominant={data.me_test.dominant_type}
      />

      {/* 마음 읽기 */}
      {profile.completed && (() => {
        const aiReportStatus: WpiAiReportStatus = aiReport?.status ?? "idle"
        const isAiReportRunning = aiReportStatus === "queued" || aiReportStatus === "processing"
        const canGenerateAiReport = !isAiReportRunning
        const aiReportButtonLabel = aiReportStatus === "completed" ? "마음 읽기 다시 생성" : "마음 읽기 생성"

        return (
          <Card>
            <CardHeader>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-2">
                  <BookOpen className="h-5 w-5 text-indigo-500" />
                  <CardTitle>마음 읽기</CardTitle>
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
            <CardContent>
              {aiReportLoading && !aiReport ? (
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
                <div className="rounded-lg border bg-muted/20 p-4 max-w-3xl leading-relaxed">
                  <MarkdownContent content={aiReport.report_md} className="text-sm" />
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  아직 생성된 마음 읽기 리포트가 없습니다. 버튼을 눌러 생성하세요.
                </p>
              )}
            </CardContent>
          </Card>
        )
      })()}

      {/* 액션 버튼 */}
      <div className="flex justify-center gap-4">
        <Button variant="outline" asChild>
          <Link to="/scan/history">이력 보기</Link>
        </Button>
        <Button asChild>
          <Link to="/scan/wpi">새 검사 시작</Link>
        </Button>
      </div>
    </div>
  )
}

export default WpiResultPage
