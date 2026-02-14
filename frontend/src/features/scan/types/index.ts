// WPI I-Test 유형 (자기평가)
export interface WpiITestScores {
  Realist: number
  Romanticist: number
  Humanist: number
  Idealist: number
  Agent: number
}

// WPI Me-Test 유형 (타인평가)
export interface WpiMeTestScores {
  Relation: number
  Trust: number
  Manual: number
  Self: number
  Culture: number
}

// WPI Gap 분석
export interface WpiGapAnalysis {
  axis_gaps: {
    relation_recognition: AxisGap
    emotion_trust: AxisGap
    social_control: AxisGap
    independence_self: AxisGap
    achievement_culture: AxisGap
  }
}

interface AxisGap {
  i_type: string
  i_score: number
  me_type: string
  me_score: number
  gap: number
}

// WPI 검사 데이터 (JSONB 구조)
export interface WpiData {
  version: number
  i_test: {
    scores: WpiITestScores
    dominant_type: string
    raw_responses: Record<string, number[]>
  } | null
  me_test: {
    scores: WpiMeTestScores
    dominant_type: string
    raw_responses: Record<string, number[]>
  } | null
  gap_analysis: WpiGapAnalysis | null
  ai_report?: {
    status: WpiAiReportStatus
    report_md: string | null
    error: string | null
    job_id: string | null
    updated_at: string | null
  }
}

export type WpiAiReportStatus = "idle" | "queued" | "processing" | "completed" | "failed"

export interface WpiAiReportResponse {
  result_id: string
  status: WpiAiReportStatus
  report_md: string | null
  error: string | null
  job_id: string | null
  updated_at: string | null
}

export interface WpiAiReportGenerateRequest {
  force_regenerate?: boolean
}

export interface WpiAiReportEnqueueResponse extends WpiAiReportResponse {
  queued: boolean
  message: string
}

// 검사 결과 (범용)
export interface ScanResult {
  id: string
  user_id: string
  scan_type: string
  completed: boolean
  created_at: string
  updated_at: string | null
  data: WpiData // 검사 유형에 따라 다른 타입
}

// 이력 목록 아이템
export interface ScanHistoryItem {
  id: string
  scan_type: string
  completed: boolean
  created_at: string
  summary: {
    dominant_i_type: string | null
    dominant_me_type: string | null
  } | null
}

// 이력 목록 응답
export interface ScanHistoryListResponse {
  items: ScanHistoryItem[]
  total: number
  limit: number
  offset: number
}

// WPI 문항
export interface WpiQuestion {
  id: number
  text: string
}

// WPI 문항 응답
export interface WpiQuestionsResponse {
  test_type: "i_test" | "me_test"
  questions: WpiQuestion[]
}

// WPI 응답 제출
export interface WpiSubmitRequest {
  test_type: "i_test" | "me_test"
  responses: {
    rank_1: number[]
    rank_2: number[]
    rank_3: number[]
  }
}

// WPI 제출 응답
export interface WpiSubmitResponse {
  status: "in_progress" | "completed"
  message: string
  result_id: string
}

// WPI 진행 상태
export interface WpiProfileStatus {
  has_profile: boolean
  has_incomplete: boolean
  i_test_completed: boolean
  me_test_completed: boolean
  in_progress_id: string | null
  created_at: string | null
}
