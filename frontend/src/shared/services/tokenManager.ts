/**
 * Token Manager - Silent Refresh 스케줄러
 *
 * Access Token 만료 전 자동 갱신을 담당합니다.
 * - TTL의 80% 시점에 proactive refresh
 * - 동시 요청 중복 제거 (single-flight)
 * - 갱신 실패 시 onAuthFailure 콜백 호출
 */

import { getRefreshToken, setAuthTokens } from './authToken'
import { authApi } from './endpoints/auth'

let refreshPromise: Promise<boolean> | null = null
let timerId: ReturnType<typeof setTimeout> | null = null
let authFailureCallback: (() => void) | null = null

/**
 * TTL의 80% 시점에 자동 갱신을 스케줄링합니다.
 */
export function start(expiresInSec: number): void {
  stop()
  const refreshAt = expiresInSec * 0.8 * 1000
  timerId = setTimeout(() => {
    timerId = null
    void refreshNow()
  }, refreshAt)
}

/**
 * 즉시 토큰을 갱신합니다. 동시 호출 시 하나의 요청만 실행됩니다.
 */
export function refreshNow(): Promise<boolean> {
  if (refreshPromise) return refreshPromise
  refreshPromise = doRefresh().finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

async function doRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    authFailureCallback?.()
    return false
  }

  try {
    const result = await authApi.refresh(refreshToken)
    setAuthTokens({
      accessToken: result.access_token,
      refreshToken: result.refresh_token,
    })
    start(result.access_expires_in)
    return true
  } catch {
    authFailureCallback?.()
    return false
  }
}

/**
 * 스케줄링된 갱신 타이머를 정리합니다.
 */
export function stop(): void {
  if (timerId !== null) {
    clearTimeout(timerId)
    timerId = null
  }
}

/**
 * 인증 실패 시 호출할 콜백을 등록합니다.
 */
export function setOnAuthFailure(callback: (() => void) | null): void {
  authFailureCallback = callback
}

export const tokenManager = {
  start,
  refreshNow,
  stop,
  setOnAuthFailure,
}
