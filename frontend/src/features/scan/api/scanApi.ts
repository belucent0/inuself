/**
 * Scan API - 심리검사 관련 API 호출
 */
import { httpClient } from "@/shared/services/api/httpClient"
import type {
  ScanResult,
  ScanHistoryListResponse,
  WpiAiReportEnqueueResponse,
  WpiAiReportGenerateRequest,
  WpiAiReportResponse,
  WpiQuestionsResponse,
  WpiSubmitRequest,
  WpiSubmitResponse,
  WpiProfileStatus,
} from "../types"

const SCAN_API_PREFIX = "/scan"

/**
 * 검사 이력 목록 조회
 */
export async function getScanHistory(params?: {
  scan_type?: string
  status?: string
  limit?: number
  offset?: number
}): Promise<ScanHistoryListResponse> {
  const searchParams = new URLSearchParams()
  if (params?.scan_type) searchParams.set("scan_type", params.scan_type)
  if (params?.status) searchParams.set("status", params.status)
  if (params?.limit) searchParams.set("limit", String(params.limit))
  if (params?.offset) searchParams.set("offset", String(params.offset))

  const query = searchParams.toString()
  return httpClient.get<ScanHistoryListResponse>(
    `${SCAN_API_PREFIX}/history${query ? `?${query}` : ""}`
  )
}

/**
 * 검사 상세 조회
 */
export async function getScanDetail(resultId: string): Promise<ScanResult> {
  return httpClient.get<ScanResult>(`${SCAN_API_PREFIX}/history/${resultId}`)
}

/**
 * WPI AI 리포트 상태/결과 조회
 */
export async function getWpiAiReport(resultId: string): Promise<WpiAiReportResponse> {
  return httpClient.get<WpiAiReportResponse>(`${SCAN_API_PREFIX}/history/${resultId}/ai-report`)
}

/**
 * WPI AI 리포트 생성 요청
 */
export async function enqueueWpiAiReport(
  resultId: string,
  data?: WpiAiReportGenerateRequest
): Promise<WpiAiReportEnqueueResponse> {
  return httpClient.post<WpiAiReportEnqueueResponse>(
    `${SCAN_API_PREFIX}/history/${resultId}/ai-report`,
    data
  )
}

/**
 * WPI 문항 조회
 */
export async function getWpiQuestions(params: {
  test_type: "i_test" | "me_test"
  shuffle?: boolean
}): Promise<WpiQuestionsResponse> {
  const searchParams = new URLSearchParams()
  searchParams.set("test_type", params.test_type)
  if (params.shuffle !== undefined) {
    searchParams.set("shuffle", String(params.shuffle))
  }

  return httpClient.get<WpiQuestionsResponse>(
    `${SCAN_API_PREFIX}/wpi/questions?${searchParams.toString()}`
  )
}

/**
 * WPI 응답 제출
 */
export async function submitWpiResponses(
  data: WpiSubmitRequest
): Promise<WpiSubmitResponse> {
  return httpClient.post<WpiSubmitResponse>(
    `${SCAN_API_PREFIX}/wpi/submit`,
    data
  )
}

/**
 * WPI 진행 상태 조회
 */
export async function getWpiStatus(): Promise<WpiProfileStatus> {
  return httpClient.get<WpiProfileStatus>(`${SCAN_API_PREFIX}/wpi/status`)
}

/**
 * WPI 최신 프로필 조회
 */
export async function getWpiProfile(): Promise<ScanResult> {
  return httpClient.get<ScanResult>(`${SCAN_API_PREFIX}/wpi/profile`)
}

/**
 * 진행 중인 WPI 검사 삭제
 */
export async function deleteWpiInProgress(): Promise<{ message: string }> {
  return httpClient.delete<{ message: string }>(`${SCAN_API_PREFIX}/wpi/in-progress`)
}

export const scanApi = {
  getScanHistory,
  getScanDetail,
  getWpiAiReport,
  enqueueWpiAiReport,
  getWpiQuestions,
  submitWpiResponses,
  getWpiStatus,
  getWpiProfile,
  deleteWpiInProgress,
}

export default scanApi
