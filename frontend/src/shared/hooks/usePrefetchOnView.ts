/**
 * 뷰포트 진입 시 prefetch 실행 훅
 * - 카드가 화면에 보이면 지연 후 상세 데이터 미리 로드
 * - 빠른 스크롤 시에는 prefetch 하지 않음 (낭비 방지)
 */

import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { contentKeys } from './useContents'
import { contentsApi } from '@/shared/services/endpoints/contents'

interface UsePrefetchOnViewOptions {
  /** prefetch 대상 콘텐츠 ID */
  contentId: string
  /** 뷰포트 진입 후 대기 시간 (ms) - 빠른 스크롤 필터링용 */
  delay?: number
  /** prefetch 활성화 여부 */
  enabled?: boolean
}

export function usePrefetchOnView({
  contentId,
  delay = 300,
  enabled = true,
}: UsePrefetchOnViewOptions) {
  const ref = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const prefetchedRef = useRef(false)

  useEffect(() => {
    if (!enabled || prefetchedRef.current || !ref.current) return

    const element = ref.current
    let timeoutId: ReturnType<typeof setTimeout> | null = null

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          // 뷰포트 진입 → 지연 후 prefetch
          timeoutId = setTimeout(() => {
            // 이미 캐시에 있으면 스킵
            const cached = queryClient.getQueryData(contentKeys.detail(contentId))
            if (cached) {
              prefetchedRef.current = true
              observer.disconnect()
              return
            }

            // prefetch 실행
            queryClient.prefetchQuery({
              queryKey: contentKeys.detail(contentId),
              queryFn: () => contentsApi.getContent(contentId),
              staleTime: 30 * 1000,
            })
            prefetchedRef.current = true
            observer.disconnect()
          }, delay)
        } else {
          // 뷰포트 이탈 → 대기 중인 prefetch 취소
          if (timeoutId) {
            clearTimeout(timeoutId)
            timeoutId = null
          }
        }
      },
      {
        threshold: 0.3, // 30% 이상 보이면 트리거
        rootMargin: '50px', // 살짝 미리 감지
      }
    )

    observer.observe(element)

    return () => {
      observer.disconnect()
      if (timeoutId) clearTimeout(timeoutId)
    }
  }, [contentId, delay, enabled, queryClient])

  return ref
}
