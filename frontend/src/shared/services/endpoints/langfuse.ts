import { httpClient } from '../api/httpClient'

export interface LangfuseSummary {
  trace_count: number
  error_count: number
  avg_latency_ms: number
  total_cost_usd: number
  avg_score: number
  score_count: number
}

export interface LangfuseTrendPoint {
  bucket: string
  request_count: number
  error_count: number
  avg_latency_ms: number
  cost_usd: number
}

export interface LangfuseOverview {
  enabled: boolean
  configured: boolean
  host: string | null
  summary: LangfuseSummary
  trend: LangfuseTrendPoint[]
  errors: string[]
}

export interface LangfuseTraceItem {
  trace_id: string
  name: string
  display_name: string
  status: string
  latency_ms: number
  cost_usd: number
  created_at: string | number | null
  project_id: string | null
  query_preview: string | null
  mode: string | null
  thread_id: string | null
  session_id: string | null
  user_id: string | null
  message_id: string | null
  user_message_id: string | null
  turn_index: number | null
  trace_path: string | null
  session_path: string | null
  input_preview: string | null
  output_preview: string | null
}

export interface LangfuseObservationItem {
  observation_id: string
  trace_id: string
  parent_observation_id: string | null
  name: string
  type: string | null
  level: string | null
  status: string
  status_message: string | null
  model: string | null
  start_time: string | number | null
  end_time: string | number | null
  latency_ms: number
  cost_usd: number
  input_preview: string | null
  output_preview: string | null
}

export interface LangfuseSessionTimeline {
  session_id: string
  trace_count: number
  traces: LangfuseTraceItem[]
}

export interface LangfuseTracesResponse {
  enabled: boolean
  configured: boolean
  traces: LangfuseTraceItem[]
  errors: string[]
}

export interface LangfuseTraceDetailResponse {
  enabled: boolean
  configured: boolean
  trace: LangfuseTraceItem | null
  observations: LangfuseObservationItem[]
  session: LangfuseSessionTimeline | null
  errors: string[]
}

export interface LangfuseSessionTimelineResponse {
  enabled: boolean
  configured: boolean
  session_id: string
  traces: LangfuseTraceItem[]
  errors: string[]
}

export async function getLangfuseOverview(
  hours = 24,
  limit = 100
): Promise<LangfuseOverview> {
  return httpClient.get<LangfuseOverview>(
    `/admin/langfuse/overview?hours=${hours}&limit=${limit}`
  )
}

export async function getLangfuseTraces(
  limit = 20
): Promise<LangfuseTracesResponse> {
  return httpClient.get<LangfuseTracesResponse>(
    `/admin/langfuse/traces?limit=${limit}`
  )
}

export async function getLangfuseTraceDetail(
  traceId: string
): Promise<LangfuseTraceDetailResponse> {
  return httpClient.get<LangfuseTraceDetailResponse>(
    `/admin/langfuse/traces/${encodeURIComponent(traceId)}`
  )
}

export async function getLangfuseSessionTimeline(
  sessionId: string,
  limit = 50
): Promise<LangfuseSessionTimelineResponse> {
  return httpClient.get<LangfuseSessionTimelineResponse>(
    `/admin/langfuse/sessions/${encodeURIComponent(sessionId)}?limit=${limit}`
  )
}

export const langfuseApi = {
  getLangfuseOverview,
  getLangfuseTraces,
  getLangfuseTraceDetail,
  getLangfuseSessionTimeline,
}
