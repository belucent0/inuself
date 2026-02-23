/**
 * 심리검사 선택 페이지
 * - WPI(현실), WPI(이상) 검사 카드 표시
 */

import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/shared/components/ui/card"
import { Button } from "@/shared/components/ui/button"
import { Badge } from "@/shared/components/ui/badge"
import { Skeleton } from "@/shared/components/ui/skeleton"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/shared/components/ui/alert-dialog"
import { useWpiStatus, useDeleteWpiInProgress } from "@/features/scan"
import { Sparkles, Play, CheckCircle2, ScanHeart, AlertTriangle } from "lucide-react"

export function ScanPage() {
  const navigate = useNavigate()
  const { status, loading, refetch: refetchStatus } = useWpiStatus()
  const { deleteInProgress, deleting } = useDeleteWpiInProgress()

  const [showIncompleteDialog, setShowIncompleteDialog] = useState(false)

  // WPI 버튼 클릭 핸들러
  const handleWpiClick = () => {
    // 진행 중인 검사가 있으면 모달 표시
    if (status?.in_progress_id) {
      setShowIncompleteDialog(true)
    } else {
      // 진행 중인 검사가 없으면 바로 검사 페이지로 이동
      navigate("/scan/wpi")
    }
  }

  // 새로 시작하기
  const handleStartNew = async () => {
    setShowIncompleteDialog(false)
    await deleteInProgress()
    await refetchStatus()
    navigate("/scan/wpi")
  }

  // 이어하기
  const handleContinue = () => {
    setShowIncompleteDialog(false)
    navigate("/scan/wpi")
  }

  // 모달 Cancel 클릭 (새로하기)
  const handleModalCancel = () => {
    handleStartNew()
  }

  // 모달 Action 클릭 (이어하기)
  const handleModalContinue = () => {
    handleContinue()
  }

  return (
    <>
      {/* 진행 중인 검사 확인 모달 */}
      <AlertDialog open={showIncompleteDialog} onOpenChange={setShowIncompleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-yellow-500" />
              진행 중인 검사가 있습니다
            </AlertDialogTitle>
            <AlertDialogDescription className="space-y-3">
              <p>
                이전에 시작한 WPI 검사가 있습니다.
              </p>
              {status?.in_progress_id && (
                <div className="text-sm text-muted-foreground">
                  <p>검사 중단일: {status.created_at ? new Date(status.created_at).toLocaleString('ko-KR') : "-"}</p>
                </div>
              )}
              <p className="text-sm text-muted-foreground">
                <strong>이어하기</strong>: 이전에 하던 검사를 이어서 진행합니다.<br />
                <strong>새로하기</strong>: 이전 검사를 삭제하고 처음부터 다시 시작합니다.
              </p>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="flex-row gap-2 sm:justify-between">
            <AlertDialogCancel disabled={deleting}>
              닫기
            </AlertDialogCancel>
            <div className="flex gap-2 ml-auto">
              <Button
                variant="outline"
                onClick={handleModalCancel}
                disabled={deleting}
              >
                새로하기
              </Button>
              <AlertDialogAction
                onClick={handleModalContinue}
                disabled={deleting}
                className="bg-red-600 text-white hover:bg-red-700"
              >
                {deleting ? "진행중..." : "이어하기"}
              </AlertDialogAction>
            </div>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <div className="container mx-auto py-6 px-2 md:px-4">
      <div className="mb-8">
        <h1 className="text-2xl font-bold mb-2">심리검사</h1>
        <p className="text-muted-foreground">
          원하시는 검사를 선택해주세요
        </p>
      </div>

      <div className="grid md:grid-cols-2 gap-6 max-w-4xl">
        {/* WPI(현실) 검사 카드 */}
        <Card className="relative overflow-hidden hover:shadow-lg transition-shadow">
          <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl from-blue-500/10 to-transparent rounded-bl-full" />
          <CardHeader>
            <div className="flex items-start justify-between">
              <div className="p-3 rounded-lg bg-blue-100 dark:bg-blue-900/30">
                <ScanHeart className="h-6 w-6 text-blue-600 dark:text-blue-400" />
              </div>
              {status?.has_profile && (
                <Badge variant="outline" className="gap-1">
                  <CheckCircle2 className="h-3 w-3" />
                  검사 완료
                </Badge>
              )}
            </div>
            <CardTitle className="text-xl mt-4">WPI 현실 검사</CardTitle>
            <CardDescription className="text-base">
              Whang's Personality Identity
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              WPI 검사는 자기 인식을 통해 삶의 어려움을 해결하는 데 도움이 되는 귀중한 통찰력을 제공합니다.
              <br /><br />
              외부 기준이 아닌, 현재 자신의 정체성과 가치, 그리고 생활 방식을 파악함으로써
              각 개인이 지닌 심리적 특성이 삶에서 어떤 양상으로 드러나고 있는지 진단할 수 있습니다.
            </p>

            {loading ? (
              <Skeleton className="h-10 w-full" />
            ) : (
              <div className="space-y-3">
                <Button className="w-full" onClick={handleWpiClick}>
                  <Play className="h-4 w-4" />
                  <span className="ml-2">검사하기</span>
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        {/* WPI(이상) 검사 카드 - 준비중 */}
        <Card className="relative overflow-hidden opacity-60">
          <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-to-bl from-purple-500/10 to-transparent rounded-bl-full" />
          <CardHeader>
            <div className="flex items-start justify-between">
              <div className="p-3 rounded-lg bg-purple-100 dark:bg-purple-900/30">
                <Sparkles className="h-6 w-6 text-purple-600 dark:text-purple-400" />
              </div>
              <Badge variant="secondary">준비중</Badge>
            </div>
            <CardTitle className="text-xl mt-4">WPI 이상 검사</CardTitle>
            <CardDescription className="text-base">
              Whang's Personality Identity(Ideal Self)
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              WPI(이상) 검사는 WPI(현실)검사와 같은 문항이지만, 자신이 이상적으로 바라는 모습을 생각하면서 답변을 하는 검사입니다.
              <br /><br />
              WPI 현실 검사와 함께 한다면, 이상적인 자신과 현실의 자신 사이의 갭을 파악할 수 있도록 돕습니다.
            </p>

            <Button disabled className="w-full">
              <Play className="h-4 w-4 mr-2" />
              준비중입니다
            </Button>
          </CardContent>
        </Card>
      </div >
    </div >
    </>
  )
}

export default ScanPage
