/**
 * SSE를 통한 파일 진행 상태 모니터링 훅
 *
 * 서버에서 /api/events/file-progress/stream을 통해
 * 파일 처리 진행 상황을 실시간으로 수신합니다.
 */

import { useEffect, useRef, useCallback, useState } from 'react'
import type { FileProgressEvent } from '@/features/upload/types'

/**
 * SSE 이벤트 리스너 콜백
 */
export type FileProgressListener = (event: FileProgressEvent) => void

/**
 * 파일 진행 상태를 SSE를 통해 구독합니다.
 *
 * @example
 * ```typescript
 * const { addListener, removeListener } = useFileProgressSSE()
 *
 * const handleProgress = (event) => {
 *   console.log(`${event.file_id}: ${event.progress}%`)
 * }
 *
 * useEffect(() => {
 *   addListener(handleProgress)
 *   return () => removeListener(handleProgress)
 * }, [addListener, removeListener])
 * ```
 */
export function useFileProgressSSE() {
  const eventSourceRef = useRef<EventSource | null>(null)
  const listenersRef = useRef<Set<FileProgressListener>>(new Set())
  const [isConnected, setIsConnected] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  /**
   * EventSource 연결 시작
   */
  const connect = useCallback(() => {
    if (eventSourceRef.current) {
      return // 이미 연결됨
    }

    try {
      const eventSource = new EventSource('/api/events/file-progress/stream')

      eventSource.onopen = () => {
        setIsConnected(true)
        setError(null)
        console.log('[SSE] Connected to file progress stream')
      }

      eventSource.onmessage = (event) => {
        try {
          // [PING] 메시지는 무시 (keep-alive)
          if (event.data === '[PING]') {
            return
          }

          const data = JSON.parse(event.data) as FileProgressEvent

          // 모든 리스너에게 이벤트 전달
          listenersRef.current.forEach((listener) => {
            try {
              listener(data)
            } catch (err) {
              console.error('[SSE] Error in listener:', err)
            }
          })
        } catch (err) {
          console.error('[SSE] Error parsing message:', err)
        }
      }

      eventSource.onerror = () => {
        setIsConnected(false)
        setError(new Error('SSE connection error'))
        eventSourceRef.current = null

        // 자동 재연결 (3초 후)
        reconnectTimeoutRef.current = setTimeout(() => {
          console.log('[SSE] Attempting to reconnect...')
          connect()
        }, 3000)
      }

      eventSourceRef.current = eventSource
    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err))
      setError(error)
      setIsConnected(false)
      console.error('[SSE] Failed to connect:', error)
    }
  }, [])

  /**
   * EventSource 연결 종료
   */
  const disconnect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
      setIsConnected(false)
      console.log('[SSE] Disconnected from file progress stream')
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
  }, [])

  /**
   * 리스너 추가
   */
  const addListener = useCallback((listener: FileProgressListener) => {
    listenersRef.current.add(listener)

    // 처음 리스너가 추가되면 연결 시작
    if (listenersRef.current.size === 1) {
      connect()
    }
  }, [connect])

  /**
   * 리스너 제거
   */
  const removeListener = useCallback((listener: FileProgressListener) => {
    listenersRef.current.delete(listener)

    // 마지막 리스너가 제거되면 연결 종료
    if (listenersRef.current.size === 0) {
      disconnect()
    }
  }, [disconnect])

  /**
   * 컴포넌트 언마운트 시 정리
   */
  useEffect(() => {
    return () => {
      disconnect()
    }
  }, [disconnect])

  return {
    isConnected,
    error,
    addListener,
    removeListener,
  }
}
