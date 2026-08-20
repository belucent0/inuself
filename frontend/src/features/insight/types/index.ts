export interface InsightPostSource {
  id: string
  title: string
  filename: string
  media_url?: string | null
  source_url?: string | null
  summary_md?: string | null
  transcript_text?: string | null
  duration_seconds: number
  speakers: string[]
}

export interface InsightEvidence {
  id: string
  source_type: 'video' | 'web' | 'document' | string
  title: string
  url?: string | null
  snippet?: string | null
  quote_text?: string | null
  timestamp_seconds?: number | null
  reliability_score?: number | null
  metadata: Record<string, unknown>
  created_at: string
}

export interface InsightAnnotation {
  id: string
  anchor_text: string
  evidence_ids: string[]
  note?: string | null
  created_at: string
}

export interface InsightPostListItem {
  id: string
  source_file_id: string
  source_title: string
  title: string
  subtitle?: string | null
  post_type: string
  tone: string
  status: string
  evidence_count: number
  created_at: string
  updated_at?: string | null
}

export interface InsightPostDetail extends InsightPostListItem {
  body_md: string
  metadata: Record<string, unknown>
  source?: InsightPostSource | null
  evidences: InsightEvidence[]
  annotations: InsightAnnotation[]
}

export interface InsightPostListResponse {
  items: InsightPostListItem[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

export interface InsightPostCreateRequest {
  post_type: string
  tone: string
  target_length: string
  include_transcript_quotes: boolean
  include_research_prompts: boolean
  allow_fallback: boolean
}

export interface InsightPostUpdateRequest {
  title?: string
  subtitle?: string | null
  body_md?: string
  status?: string
  metadata?: Record<string, unknown>
}

export interface InsightResearchRequest {
  query?: string | null
  max_results: number
  append_to_body: boolean
}
