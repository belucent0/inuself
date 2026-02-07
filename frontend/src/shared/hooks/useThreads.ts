/**
 * Thread 관리 Hook
 *
 * SOLID 원칙:
 * - Single Responsibility: Thread 상태 관리와 API 호출 로직 분리
 * - Interface Segregation: 필요한 기능만 노출
 */

import { useState, useEffect, useCallback } from 'react'
import { toast } from 'sonner'
import { threadsApi } from '@/shared/services'
import type { Thread } from '@/shared/types'

export function useThreads() {
  const [threads, setThreads] = useState<Thread[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  // 스레드 목록 로드
  const loadThreads = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const response = await threadsApi.getThreads()
      setThreads(response.threads)
    } catch (err) {
      const error = err as Error
      setError(error)
      toast.error('대화 목록을 불러오지 못했습니다', {
        description: error.message,
      })
    } finally {
      setIsLoading(false)
    }
  }, [])

  // 초기 로드
  useEffect(() => {
    loadThreads()
  }, [loadThreads])

  // 새 스레드 생성
  const createNewThread = useCallback(async (title?: string) => {
    try {
      const newThread = await threadsApi.createThread({ title })
      setThreads((prev) => [newThread, ...prev])
      return newThread
    } catch (err) {
      const error = err as Error
      toast.error('새 대화를 생성하지 못했습니다', {
        description: error.message,
      })
      throw error
    }
  }, [])

  // 스레드 삭제
  const removeThread = useCallback(async (threadId: string) => {
    try {
      await threadsApi.deleteThread(threadId)
      setThreads((prev) => prev.filter((t) => t.thread_id !== threadId))
      toast.success('대화가 삭제되었습니다')
    } catch (err) {
      const error = err as Error
      toast.error('대화를 삭제하지 못했습니다', {
        description: error.message,
      })
      throw error
    }
  }, [])

  // 스레드 제목 변경
  const changeThreadTitle = useCallback(async (threadId: string, title: string) => {
    try {
      const updatedThread = await threadsApi.updateThreadTitle(threadId, title)
      setThreads((prev) => prev.map((t) => (t.thread_id === threadId ? updatedThread : t)))
      toast.success('제목이 변경되었습니다')
    } catch (err) {
      const error = err as Error
      toast.error('제목을 변경하지 못했습니다', {
        description: error.message,
      })
      throw error
    }
  }, [])

  return {
    threads,
    isLoading,
    error,
    loadThreads,
    createNewThread,
    removeThread,
    changeThreadTitle,
  }
}

export function useThread(threadId: string | null) {
  const [thread, setThread] = useState<Thread | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const loadThread = useCallback(async () => {
    if (!threadId) {
      setThread(null)
      return
    }

    setIsLoading(true)
    setError(null)
    try {
      const data = await threadsApi.getThread(threadId)
      setThread(data)
    } catch (err) {
      const error = err as Error
      setError(error)
      toast.error('대화를 불러오지 못했습니다', {
        description: error.message,
      })
    } finally {
      setIsLoading(false)
    }
  }, [threadId])

  useEffect(() => {
    loadThread()
  }, [loadThread])

  return {
    thread,
    isLoading,
    error,
    reloadThread: loadThread,
    updateThread: setThread,
  }
}
