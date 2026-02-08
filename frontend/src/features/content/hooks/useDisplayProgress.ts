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

const ESTIMATED_DURATION: Partial<Record<ContentStatus, (d: number) => number>> = {
  PROCESSING: (duration) => (duration || 120) * 0.65,
  OCR_PROCESSING: () => 60,
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
      let raw: number

      if (isModeB) {
        // Mode B: SSE 마일스톤 보간
        const { value: anchorVal, time: anchorTime } = anchorRef.current
        if (anchorTime <= 0) {
          raw = realProgress
        } else {
          const elapsed = (Date.now() - anchorTime) / 1000
          const interpolated = anchorVal + elapsed * INTERPOLATION_RATE
          // ease-out: 버퍼에 가까워질수록 느려짐
          const bufferRatio = Math.min(
            (interpolated - anchorVal) / MAX_INTERPOLATION_BUFFER,
            1,
          )
          const eased = anchorVal + MAX_INTERPOLATION_BUFFER * (1 - Math.pow(1 - bufferRatio, 2))
          raw = Math.min(eased, 99)
        }
      } else {
        // Mode A: 시간 기반 추정
        const estimator = ESTIMATED_DURATION[status]
        if (!estimator) {
          raw = PROGRESS_MAP[status] ?? 0
        } else {
          const estimatedTotal = estimator(duration_seconds)
          const startTime = updated_at ? new Date(updated_at).getTime() : 0

          if (!startTime || startTime <= 0) {
            raw = PROGRESS_MAP[status] ?? 10
          } else {
            const elapsed = (Date.now() - startTime) / 1000
            const ratio = Math.max(0, Math.min(elapsed / estimatedTotal, 1))
            const eased = 1 - Math.pow(1 - ratio, 3)
            raw = 10 + eased * 75
          }
        }
      }

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
  }, [isProcessing, realProgress, status, duration_seconds, updated_at])

  return display
}
