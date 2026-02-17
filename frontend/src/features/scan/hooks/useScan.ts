/**
 * Scan 관련 훅
 */
import { useState, useEffect, useCallback } from "react"
import { scanApi } from "../api/scanApi"
import type {
  ScanResult,
  ScanHistoryItem,
  WpiAiReportEnqueueResponse,
  WpiAiReportResponse,
  WpiQuestionsResponse,
  WpiSubmitRequest,
  WpiSubmitResponse,
  WpiProfileStatus,
} from "../types"

function toError(err: unknown, fallback: string): Error {
  if (err instanceof Error) {
    return err
  }
  return new Error(fallback)
}

/**
 * WPI 진행 상태 조회 훅
 */
export function useWpiStatus() {
  const [status, setStatus] = useState<WpiProfileStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  const fetchStatus = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await scanApi.getWpiStatus()
      setStatus(data)
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Failed to fetch status"))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchStatus()
  }, [fetchStatus])

  return { status, loading, error, refetch: fetchStatus }
}

/**
 * WPI 문항 조회 훅
 */
export function useWpiQuestions(testType: "i_test" | "me_test", shuffle = true) {
  const [questions, setQuestions] = useState<WpiQuestionsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  const fetchQuestions = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await scanApi.getWpiQuestions({ test_type: testType, shuffle })
      setQuestions(data)
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Failed to fetch questions"))
    } finally {
      setLoading(false)
    }
  }, [testType, shuffle])

  useEffect(() => {
    fetchQuestions()
  }, [fetchQuestions])

  return { questions, loading, error, refetch: fetchQuestions }
}

/**
 * WPI 응답 제출 훅
 */
export function useWpiSubmit() {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [result, setResult] = useState<WpiSubmitResponse | null>(null)

  const submit = useCallback(async (data: WpiSubmitRequest) => {
    setSubmitting(true)
    setError(null)
    setResult(null)
    try {
      const response = await scanApi.submitWpiResponses(data)
      setResult(response)
      return response
    } catch (err) {
      const error = err instanceof Error ? err : new Error("Failed to submit")
      setError(error)
      throw error
    } finally {
      setSubmitting(false)
    }
  }, [])

  return { submit, submitting, error, result }
}

/**
 * WPI 프로필 조회 훅
 */
export function useWpiProfile() {
  const [profile, setProfile] = useState<ScanResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  const fetchProfile = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await scanApi.getWpiProfile()
      setProfile(data)
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Failed to fetch profile"))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchProfile()
  }, [fetchProfile])

  return { profile, loading, error, refetch: fetchProfile }
}

/**
 * 검사 이력 목록 조회 훅
 */
export function useScanHistory(params?: {
  scan_type?: string
  status?: string
  limit?: number
  offset?: number
}) {
  const [items, setItems] = useState<ScanHistoryItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  const fetchHistory = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await scanApi.getScanHistory(params)
      setItems(data.items)
      setTotal(data.total)
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Failed to fetch history"))
    } finally {
      setLoading(false)
    }
  }, [params?.scan_type, params?.status, params?.limit, params?.offset])

  useEffect(() => {
    fetchHistory()
  }, [fetchHistory])

  return { items, total, loading, error, refetch: fetchHistory }
}

/**
 * 검사 상세 조회 훅
 */
export function useScanDetail(resultId: string | null) {
  const [detail, setDetail] = useState<ScanResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const fetchDetail = useCallback(async () => {
    if (!resultId) {
      setDetail(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await scanApi.getScanDetail(resultId)
      setDetail(data)
    } catch (err) {
      setError(err instanceof Error ? err : new Error("Failed to fetch detail"))
    } finally {
      setLoading(false)
    }
  }, [resultId])

  useEffect(() => {
    fetchDetail()
  }, [fetchDetail])

  return { detail, loading, error, refetch: fetchDetail }
}

/**
 * WPI AI 리포트 조회/생성 훅
 */
export function useWpiAiReport(resultId: string | null) {
  const [aiReport, setAiReport] = useState<WpiAiReportResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const fetchAiReport = useCallback(async () => {
    if (!resultId) {
      setAiReport(null)
      return
    }

    setLoading(true)
    setError(null)
    try {
      const data = await scanApi.getWpiAiReport(resultId)
      setAiReport(data)
    } catch (err) {
      setError(toError(err, "Failed to fetch AI report"))
    } finally {
      setLoading(false)
    }
  }, [resultId])

  const enqueueAiReport = useCallback(
    async (forceRegenerate = false): Promise<WpiAiReportEnqueueResponse> => {
      if (!resultId) {
        throw new Error("Missing result ID")
      }

      setGenerating(true)
      setError(null)
      try {
        const data = await scanApi.enqueueWpiAiReport(resultId, {
          force_regenerate: forceRegenerate,
        })
        setAiReport(data)
        return data
      } catch (err) {
        const nextError = toError(err, "Failed to enqueue AI report")
        setError(nextError)
        throw nextError
      } finally {
        setGenerating(false)
      }
    },
    [resultId]
  )

  useEffect(() => {
    fetchAiReport()
  }, [fetchAiReport])

  useEffect(() => {
    if (!resultId || !aiReport) {
      return
    }

    if (aiReport.status !== "queued" && aiReport.status !== "processing") {
      return
    }

    const timer = window.setInterval(() => {
      void fetchAiReport()
    }, 2000)

    return () => window.clearInterval(timer)
  }, [resultId, aiReport, fetchAiReport])

  return {
    aiReport,
    loading,
    generating,
    error,
    refetch: fetchAiReport,
    enqueueAiReport,
  }
}

/**
 * 진행 중인 WPI 검사 삭제 훅
 */
export function useDeleteWpiInProgress() {
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const deleteInProgress = useCallback(async () => {
    setDeleting(true)
    setError(null)
    try {
      await scanApi.deleteWpiInProgress()
      return true
    } catch (err) {
      const error = err instanceof Error ? err : new Error("Failed to delete")
      setError(error)
      return false
    } finally {
      setDeleting(false)
    }
  }, [])

  return { deleteInProgress, deleting, error }
}
