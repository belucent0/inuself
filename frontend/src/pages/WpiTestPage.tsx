/**
 * WPI 검사 진행 페이지
 * - 30문항 카드 그리드 (데스크톱 5열, 모바일 2열)
 * - 순위별 선택 제한 (1순위 3개, 2순위 4개, 3순위 5개)
 * - 반응형 선택 현황 표시
 */

import { useState, useMemo, useEffect } from "react"
import { useNavigate } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card"
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
import { useWpiStatus, useWpiQuestions, useWpiSubmit, useDeleteWpiInProgress } from "@/features/scan"
import type { WpiQuestion } from "@/features/scan"
import { cn } from "@/shared/utils/cn"
import { X, Check, AlertTriangle } from "lucide-react"

// 순위별 설정
const RANK_CONFIG = {
  rank_1: { label: "1순위", max: 3, color: "bg-indigo-600", textColor: "text-indigo-600", borderColor: "border-indigo-600", hoverColor: "hover:bg-indigo-600" },
  rank_2: { label: "2순위", max: 4, color: "bg-violet-600", textColor: "text-violet-600", borderColor: "border-violet-600", hoverColor: "hover:bg-violet-600" },
  rank_3: { label: "3순위", max: 5, color: "bg-rose-600", textColor: "text-rose-600", borderColor: "border-rose-600", hoverColor: "hover:bg-rose-600" },
} as const

type RankKey = keyof typeof RANK_CONFIG

// 안내 문구
const GUIDE_TEXT = {
  i_test: {
    title: "자기 평가 성격 체크리스트",
    description:
      "\"내가 생각하는 나\"는 어떤 사람일까요? 아래에는 '성격'에 대한 다양한 생각과 행동을 표현한 문항들이 있습니다. 자신의 성격을 가장 잘 나타내는 문항을 순위를 구분하여 선택해주세요.",
  },
  me_test: {
    title: "타인 평가 성격 체크리스트",
    description:
      "\"주변 사람이 보기에 나\"는 어떤 사람일까요? 주변 사람들의 관점에서 나를 생각할 때, 나의 성격을 가장 잘 나타낼 것 같은 문항을 순위를 구분하여 스스로 선택해주세요.",
  },
} as const

export function WpiTestPage() {
  const navigate = useNavigate()
  const { status, loading: statusLoading, refetch: refetchStatus } = useWpiStatus()
  const { submit, submitting } = useWpiSubmit()
  const { deleteInProgress, deleting } = useDeleteWpiInProgress()

  // 현재 테스트 타입을 로컬 상태로 관리 (제출 후 명시적으로 전환)
  const [currentTestType, setCurrentTestType] = useState<"i_test" | "me_test">("i_test")

  // 미완료 검사 확인 모달 상태
  const [showIncompleteDialog, setShowIncompleteDialog] = useState(false)
  const [hasCheckedIncomplete, setHasCheckedIncomplete] = useState(false)
  // 새로 시작하기를 선택한 경우 리다이렉트 방지
  const [isStartingNew, setIsStartingNew] = useState(false)

  // 미완료 검사 확인 (페이지 진입 시 한번만)
  useEffect(() => {
    if (status && !hasCheckedIncomplete) {
      setHasCheckedIncomplete(true)
      // 미완료 검사가 있고, 아직 모달을 보여주지 않았으면
      if (status.in_progress_id) {
        setShowIncompleteDialog(true)
      }
    }
  }, [status, hasCheckedIncomplete])

  // 초기 테스트 타입 설정
  useEffect(() => {
    // 새로 시작하기를 선택한 경우 리다이렉트 하지 않음
    if (isStartingNew) return

    if (status) {
      if (!status.i_test_completed) {
        setCurrentTestType("i_test")
      } else if (!status.me_test_completed) {
        setCurrentTestType("me_test")
      }
      // 진행 중인 검사가 없고, 둘 다 완료 상태이면 결과 페이지로 (has_profile인 경우)
      // 단, 새 검사 시작 직후에는 리다이렉트하지 않음
      if (status.has_profile && !status.in_progress_id && !status.has_incomplete) {
        navigate("/scan/wpi/result")
      }
    }
  }, [status, navigate, isStartingNew])

  // 새로 시작하기 핸들러
  const handleStartNew = async () => {
    setIsStartingNew(true)
    setShowIncompleteDialog(false)
    await deleteInProgress()
    setCurrentTestType("i_test")
  }

  // 이어하기 핸들러
  const handleContinue = () => {
    setShowIncompleteDialog(false)
    // 현재 상태에 맞게 테스트 타입 설정
    if (status?.i_test_completed && !status?.me_test_completed) {
      setCurrentTestType("me_test")
    } else {
      setCurrentTestType("i_test")
    }
  }

  // 문항을 ID 순으로 정렬하여 가져옴 (shuffle=false)
  const { questions, loading: questionsLoading } = useWpiQuestions(currentTestType, false)

  // 문항을 ID순으로 정렬 (훅은 항상 최상위에서 호출)
  const sortedQuestions = useMemo(() => {
    if (!questions?.questions) return []
    return [...questions.questions].sort((a, b) => a.id - b.id)
  }, [questions])

  // 응답 상태
  const [responses, setResponses] = useState<Record<RankKey, number[]>>({
    rank_1: [],
    rank_2: [],
    rank_3: [],
  })

  // 문항의 현재 순위 가져오기
  const getQuestionRank = (questionId: number): RankKey | null => {
    if (responses.rank_1.includes(questionId)) return "rank_1"
    if (responses.rank_2.includes(questionId)) return "rank_2"
    if (responses.rank_3.includes(questionId)) return "rank_3"
    return null
  }

  // 순위가 가득 찼는지 확인
  const isRankFull = (rank: RankKey): boolean => {
    return responses[rank].length >= RANK_CONFIG[rank].max
  }

  // 순위 선택/변경 핸들러
  const handleSelect = (questionId: number, rank: RankKey) => {
    const currentRank = getQuestionRank(questionId)

    // 이미 같은 순위에 선택되어 있으면 해제
    if (currentRank === rank) {
      handleDeselect(questionId)
      return
    }

    // 해당 순위가 가득 찼으면 무시
    if (isRankFull(rank)) return

    setResponses((prev) => {
      const newResponses = { ...prev }

      // 다른 순위에서 제거
      if (currentRank) {
        newResponses[currentRank] = newResponses[currentRank].filter((id) => id !== questionId)
      }

      // 새 순위에 추가
      newResponses[rank] = [...newResponses[rank], questionId]

      return newResponses
    })
  }

  // 선택 해제 핸들러
  const handleDeselect = (questionId: number) => {
    setResponses((prev) => {
      const newResponses = { ...prev }
      ;(Object.keys(newResponses) as RankKey[]).forEach((key) => {
        newResponses[key] = newResponses[key].filter((id) => id !== questionId)
      })
      return newResponses
    })
  }

  // 완료 여부
  const isComplete =
    responses.rank_1.length === RANK_CONFIG.rank_1.max &&
    responses.rank_2.length === RANK_CONFIG.rank_2.max &&
    responses.rank_3.length === RANK_CONFIG.rank_3.max

  // 제출
  const handleSubmit = async () => {
    if (!isComplete) return

    try {
      const result = await submit({
        test_type: currentTestType,
        responses,
      })

      if (result.status === "completed") {
        // 모든 검사 완료 - 결과 페이지로 이동
        navigate(`/scan/history/${result.result_id}`)
      } else {
        // 타인평가로 전환
        setCurrentTestType("me_test")
        setResponses({ rank_1: [], rank_2: [], rank_3: [] })
        // 상태 새로고침
        await refetchStatus()
      }
    } catch {
      // 에러는 useWpiSubmit에서 처리
    }
  }

  if (statusLoading || questionsLoading) {
    return (
      <div className="container mx-auto py-6 space-y-6">
        <Skeleton className="h-24 w-full" />
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          {Array(30)
            .fill(0)
            .map((_, i) => (
              <Skeleton key={i} className="h-32" />
            ))}
        </div>
      </div>
    )
  }

  const guide = GUIDE_TEXT[currentTestType]

  // 미완료 검사 확인 모달을 표시 중이면 검사 UI를 숨김
  if (showIncompleteDialog) {
    return (
      <AlertDialog open={showIncompleteDialog} onOpenChange={setShowIncompleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-yellow-500" />
              이전에 검사하던 내역이 있습니다
            </AlertDialogTitle>
            <AlertDialogDescription className="space-y-2">
              <p>
                {status?.i_test_completed
                  ? "자기평가를 완료하고 타인평가를 진행 중이었습니다."
                  : "자기평가를 진행 중이었습니다."}
              </p>
              <p className="text-sm text-muted-foreground">
                <strong>이어하기</strong>: 이전에 하던 검사를 이어서 진행합니다.<br />
                <strong>새로하기</strong>: 이전 검사를 삭제하고 처음부터 다시 시작합니다.
              </p>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={handleContinue} disabled={deleting}>
              이어하기
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleStartNew}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? "삭제 중..." : "새로하기"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    )
  }

  return (
    <div className="container mx-auto py-6 px-4">
      {/* 안내 문구 */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold mb-2">{guide.title}</h1>
        <p className="text-muted-foreground">{guide.description}</p>
        <div className="mt-3 flex gap-4 text-sm">
          <span className="text-indigo-600 font-medium">● 1순위: 3개</span>
          <span className="text-violet-600 font-medium">● 2순위: 4개</span>
          <span className="text-rose-600 font-medium">● 3순위: 5개</span>
        </div>
      </div>

      {/* 모바일: sticky 선택 현황 바 */}
      <MobileSelectionBar responses={responses} />

      <div className="flex gap-6">
        {/* 문항 그리드 */}
        <div className="flex-1">
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-3 pb-24 lg:pb-6">
            {sortedQuestions.map((question) => (
              <QuestionCard
                key={question.id}
                question={question}
                currentRank={getQuestionRank(question.id)}
                onSelect={handleSelect}
                isRankFull={isRankFull}
              />
            ))}
          </div>
        </div>

        {/* 데스크톱: sticky 사이드바 */}
        <div className="hidden lg:block w-72 flex-shrink-0">
          <div className="sticky top-6 space-y-4">
            <SelectionPanel
              responses={responses}
              questions={sortedQuestions}
              onRemove={handleDeselect}
            />
            <Button
              className="w-full"
              size="lg"
              disabled={!isComplete || submitting}
              onClick={handleSubmit}
            >
              {submitting ? "제출 중..." : isComplete ? "제출하기" : `${getTotalSelected(responses)}/12 선택됨`}
            </Button>
          </div>
        </div>
      </div>

      {/* 모바일: 하단 고정 제출 버튼 */}
      <div className="lg:hidden fixed bottom-0 left-0 right-0 p-4 bg-background border-t">
        <Button
          className="w-full"
          size="lg"
          disabled={!isComplete || submitting}
          onClick={handleSubmit}
        >
          {submitting ? "제출 중..." : isComplete ? "제출하기" : `${getTotalSelected(responses)}/12 선택됨`}
        </Button>
      </div>
    </div>
  )
}

// 선택된 총 개수
function getTotalSelected(responses: Record<RankKey, number[]>): number {
  return responses.rank_1.length + responses.rank_2.length + responses.rank_3.length
}

// 문항 카드 컴포넌트
function QuestionCard({
  question,
  currentRank,
  onSelect,
  isRankFull,
}: {
  question: WpiQuestion
  currentRank: RankKey | null
  onSelect: (id: number, rank: RankKey) => void
  isRankFull: (rank: RankKey) => boolean
}) {
  return (
    <Card
      className={cn(
        "p-4 transition-all",
        currentRank && `ring-2 ${RANK_CONFIG[currentRank].borderColor}`
      )}
    >
      <p className="text-sm text-muted-foreground mb-1 font-semibold">#{question.id}</p>
      <p className="text-base line-clamp-3 min-h-[4.5rem] mb-3 font-medium">{question.text}</p>
      <div className="flex gap-1">
        {(["rank_1", "rank_2", "rank_3"] as const).map((rank) => {
          const isSelected = currentRank === rank
          const isFull = isRankFull(rank)
          const isDisabled = isFull && !isSelected
          const config = RANK_CONFIG[rank]
          const rankNumber = rank === "rank_1" ? "1" : rank === "rank_2" ? "2" : "3"

          return (
            <Button
              key={rank}
              size="sm"
              variant="outline"
              disabled={isDisabled}
              className={cn(
                "flex-1 h-10 relative transition-all font-semibold text-base",
                // 선택된 경우: 해당 순위의 배경색으로 진하게
                isSelected && config.color,
                isSelected && "text-white border-transparent",
                // 선택 가능한 경우: 테두리와 텍스트만 색상 적용
                !isSelected && !isDisabled && config.textColor,
                !isSelected && !isDisabled && config.borderColor,
                !isSelected && !isDisabled && "bg-transparent",
                // 비활성화된 경우 (순위 가득 참)
                isDisabled && "!bg-gray-100 !text-gray-300 !border-gray-200 cursor-not-allowed opacity-50"
              )}
              onClick={() => onSelect(question.id, rank)}
            >
              {isSelected ? (
                <span className="flex items-center gap-1">
                  <Check className="h-4 w-4" />
                  {rankNumber}
                </span>
              ) : (
                rankNumber
              )}
            </Button>
          )
        })}
      </div>
    </Card>
  )
}

// 모바일 선택 현황 바 (2행: 순위명 / 선택 문항)
function MobileSelectionBar({ responses }: { responses: Record<RankKey, number[]> }) {
  return (
    <div className="lg:hidden sticky top-0 z-10 bg-background/95 backdrop-blur border-b mb-4 -mx-4 px-4 py-3">
      <div className="flex items-start justify-around gap-2">
        {(["rank_1", "rank_2", "rank_3"] as const).map((rank) => {
          const config = RANK_CONFIG[rank]
          const selected = responses[rank]
          const total = config.max

          return (
            <div key={rank} className="flex flex-col items-center gap-1.5 flex-1">
              {/* 1행: 순위명과 카운트 */}
              <span className={cn("text-xs font-semibold", config.textColor)}>
                {config.label} ({selected.length}/{total})
              </span>
              {/* 2행: 선택된 문항 뱃지 */}
              <div className="flex flex-wrap gap-1 justify-center min-h-[26px]">
                {selected.length > 0
                  ? selected.sort((a, b) => a - b).map(id => (
                      <div
                        key={id}
                        className={cn(
                          "w-6 h-6 flex items-center justify-center rounded border-2 text-xs font-semibold",
                          config.borderColor,
                          config.textColor,
                          "bg-white"
                        )}
                      >
                        {id}
                      </div>
                    ))
                  : <span className="text-muted-foreground text-xs">-</span>
                }
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// 데스크톱 선택 패널
function SelectionPanel({
  responses,
  questions,
  onRemove,
}: {
  responses: Record<RankKey, number[]>
  questions: WpiQuestion[]
  onRemove: (id: number) => void
}) {
  const getQuestionText = (id: number) => {
    const q = questions.find((q) => q.id === id)
    return q?.text || ""
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">현재 선택 현황</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {(["rank_1", "rank_2", "rank_3"] as const).map((rank) => {
          const config = RANK_CONFIG[rank]
          const selected = responses[rank]
          const emptySlots = config.max - selected.length

          return (
            <div key={rank}>
              <p className={cn("text-sm font-medium mb-2", config.textColor)}>
                {config.label} ({selected.length}/{config.max})
              </p>
              <div className="flex flex-wrap gap-2">
                {selected.map((id) => (
                  <div
                    key={id}
                    className={cn(
                      "px-2 py-1 rounded border-2 flex items-center gap-1 cursor-pointer transition-opacity hover:opacity-80",
                      config.borderColor,
                      config.textColor,
                      "bg-white text-sm font-semibold"
                    )}
                    onClick={() => onRemove(id)}
                    title={getQuestionText(id)}
                  >
                    #{id}
                    <X className="h-4 w-4" />
                  </div>
                ))}
                {Array(emptySlots)
                  .fill(0)
                  .map((_, i) => (
                    <div
                      key={`empty-${i}`}
                      className="px-2 py-1 rounded border-2 border-gray-200 text-gray-300 opacity-30 cursor-default text-sm font-semibold"
                    >
                      -
                    </div>
                  ))}
              </div>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}

export default WpiTestPage
