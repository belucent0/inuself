/**
 * 통합 진행률 훅
 *
 * Mode A (Estimation): SSE progress가 없을 때 updated_at 기반 시간 추정
 * Mode B (Interpolation): SSE 마일스톤 사이에서 0.3%/초 보간
 *
 * highWaterMark로 단조증가를 보장하여 역행 방지.
 */

import { useState, useEffect, useRef } from 'react'
import type { ContentSummary, ContentStatus } from '../types'

const PROCESSING_STATUSES: ContentStatus[] = [
  'QUEUED', 'PULLING', 'PROCESSING', 'OCR_PROCESSING', 'SUMMARY_QUEUED', 'SUMMARIZING',
]

const PROGRESS_MAP: Partial<Record<ContentStatus, number>> = {
  QUEUED: 0,
  PULLING: 0,
  PROCESSING: 10,
  OCR_PROCESSING: 10,
  SUMMARY_QUEUED: 0,
  SUMMARIZING: 10,
}

/** 상태별 예상 소요시간(초) 계산. content 전체를 받아 정확한 추정. */
type DurationEstimator = (content: ContentSummary) => number

const ESTIMATED_DURATION: Partial<Record<ContentStatus, DurationEstimator>> = {
  PROCESSING: (c) => (c.duration_seconds || 120) * 1.5,
  OCR_PROCESSING: (c) => Math.max((c.document?.page_count ?? 0), 1) * 30,
  SUMMARIZING: () => 45,
}

/** interpolation 속도: %/초 */
const INTERPOLATION_RATE = 0.3
/** interpolation 최대 버퍼 (anchor 대비 최대 추가 %) */
const MAX_INTERPOLATION_BUFFER = 8

interface AnchorState {
  value: number
  time: number
}

export function useDisplayProgress(content: ContentSummary): number {
  const { status, progress: sseProgress, duration_seconds, updated_at } = content

  const [display, setDisplay] = useState(0)
  const anchorRef = useRef<AnchorState>({ value: 0, time: 0 })
  const highWaterRef = useRef(0)
  const prevContentIdRef = useRef(content.id)
  const prevStatusRef = useRef(status)

  // 콘텐츠 변경 시 ref 초기화
  if (prevContentIdRef.current !== content.id) {
    prevContentIdRef.current = content.id
    prevStatusRef.current = status
    anchorRef.current = { value: 0, time: 0 }
    highWaterRef.current = 0
  }

  // 상태 전환 시 ref 초기화 (PULLING→PROCESSING 등)
  if (prevStatusRef.current !== status) {
    prevStatusRef.current = status
    anchorRef.current = { value: 0, time: 0 }
    highWaterRef.current = 0
  }

  const isProcessing = PROCESSING_STATUSES.includes(status)
  const realProgress = sseProgress ?? 0

  // 비처리 상태 → 즉시 반환
  useEffect(() => {
    if (!isProcessing) {
      anchorRef.current = { value: 0, time: 0 }
      highWaterRef.current = 0
      setDisplay(status === 'COMPLETED' ? 100 : 0)
    }
  }, [isProcessing, status])

  // SSE anchor 갱신 (Mode B 준비)
  useEffect(() => {
    if (isProcessing && realProgress > 0 && realProgress !== anchorRef.current.value) {
      anchorRef.current = { value: realProgress, time: Date.now() }
    }
  }, [isProcessing, realProgress])

  // 메인 tick 루프
  useEffect(() => {
    if (!isProcessing) return

    const isModeB = realProgress > 0

    const tick = () => {
      // Mode A: 시간 기반 추정 (항상 계산)
      let modeA = 0
      const estimator = ESTIMATED_DURATION[status]
      if (!estimator) {
        modeA = PROGRESS_MAP[status] ?? 0
      } else {
        const estimatedTotal = estimator(content)
        const startTime = updated_at ? new Date(updated_at).getTime() : 0

        if (!startTime || startTime <= 0) {
          modeA = PROGRESS_MAP[status] ?? 10
        } else {
          const elapsed = (Date.now() - startTime) / 1000
          const ratio = Math.max(0, elapsed / estimatedTotal)

          if (ratio <= 1) {
            // 추정시간 이내: quadratic ease-out (cubic보다 완만)
            const eased = 1 - Math.pow(1 - ratio, 2)
            modeA = 10 + eased * 70
          } else {
            // 추정시간 초과: 로그 크롤 (80% → 95% 점근, 절대 멈추지 않음)
            const overtime = ratio - 1
            const extra = 15 * (1 - 1 / (1 + overtime * 0.5))
            modeA = 80 + extra
          }
        }
      }

      // Mode B: SSE 마일스톤 보간 (SSE 이벤트가 있을 때만)
      let modeB = 0
      if (isModeB) {
        const { value: anchorVal, time: anchorTime } = anchorRef.current
        if (anchorTime <= 0) {
          modeB = realProgress
        } else {
          const elapsed = (Date.now() - anchorTime) / 1000
          const interpolated = anchorVal + elapsed * INTERPOLATION_RATE
          // ease-out: 버퍼에 가까워질수록 느려짐
          const bufferRatio = Math.min(
            (interpolated - anchorVal) / MAX_INTERPOLATION_BUFFER,
            1,
          )
          modeB = anchorVal + MAX_INTERPOLATION_BUFFER * (1 - Math.pow(1 - bufferRatio, 2))
          modeB = Math.min(modeB, 99)
        }
      }

      // 두 모드 중 더 높은 값 사용 (NPU 구간에서 Mode B 버퍼 소진 시 Mode A가 이어받음)
      const raw = Math.max(modeA, modeB)

      // 단조증가 보장
      const clamped = Math.max(0, Math.min(100, Math.round(raw)))
      if (clamped > highWaterRef.current) {
        highWaterRef.current = clamped
      }
      setDisplay(highWaterRef.current)
    }

    tick()
    const interval = isModeB ? 200 : 1000
    const id = setInterval(tick, interval)
    return () => clearInterval(id)
  }, [isProcessing, realProgress, status, duration_seconds, updated_at, content.document?.page_count])

  return display
}
