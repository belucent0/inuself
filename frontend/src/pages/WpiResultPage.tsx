/**
 * WPI 검사 결과 페이지
 * - 프로파일 차트
 * - Gap 분석
 */

import { Link } from "react-router-dom"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/components/ui/card"
import { Button } from "@/shared/components/ui/button"
import { Badge } from "@/shared/components/ui/badge"
import { Skeleton } from "@/shared/components/ui/skeleton"
import { useWpiProfile, WpiResultChart } from "@/features/scan"
import type { WpiData, WpiITestScores, WpiMeTestScores } from "@/features/scan"
import { formatToKST } from "@/shared/utils/cn"
import { ArrowLeft, TrendingUp, TrendingDown, Minus } from "lucide-react"

// Gap 해석 함수
function interpretGap(gap: number): { icon: React.ReactNode; text: string; color: string } {
  if (gap > 5) {
    return {
      icon: <TrendingUp className="h-4 w-4" />,
      text: "자기인식이 높음",
      color: "text-blue-600",
    }
  } else if (gap < -5) {
    return {
      icon: <TrendingDown className="h-4 w-4" />,
      text: "타인인식이 높음",
      color: "text-red-600",
    }
  }
  return {
    icon: <Minus className="h-4 w-4" />,
    text: "균형",
    color: "text-green-600",
  }
}

export function WpiResultPage() {
  const { profile, loading, error } = useWpiProfile()

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

      {/* Gap 분석 */}
      {data.gap_analysis && (
        <Card>
          <CardHeader>
            <CardTitle>Gap 분석</CardTitle>
            <CardDescription>
              자기평가와 타인평가 간의 차이를 분석합니다
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {Object.entries(data.gap_analysis.axis_gaps).map(([key, axis]) => {
                const interpretation = interpretGap(axis.gap)

                return (
                  <div
                    key={key}
                    className="flex items-center justify-between p-4 rounded-lg bg-muted/50"
                  >
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-medium">{axis.i_type}</span>
                        <span className="text-muted-foreground">↔</span>
                        <span className="font-medium">{axis.me_type}</span>
                      </div>
                      <div className="flex items-center gap-4 text-sm text-muted-foreground">
                        <span>자기평가: {axis.i_score.toFixed(1)}</span>
                        <span>타인평가: {axis.me_score.toFixed(1)}</span>
                      </div>
                    </div>
                    <div className="text-right">
                      <div
                        className={`flex items-center gap-1 font-medium ${interpretation.color}`}
                      >
                        {interpretation.icon}
                        <span>Gap: {axis.gap > 0 ? "+" : ""}{axis.gap.toFixed(1)}</span>
                      </div>
                      <p className={`text-sm ${interpretation.color}`}>
                        {interpretation.text}
                      </p>
                    </div>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}

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
