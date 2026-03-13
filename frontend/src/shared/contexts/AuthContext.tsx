import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { authApi, type AuthTokenResponse, type AuthUser, type LoginRequest, type SignupRequest } from '@/shared/services/endpoints/auth'
import { clearAuthTokens, getRefreshToken, setAuthTokens } from '@/shared/services/authToken'
import { tokenManager } from '@/shared/services/tokenManager'
import { queryClient } from '@/shared/lib/queryClient'
import { useChatStore } from '@/shared/stores/chatStore'

interface AuthContextValue {
  user: AuthUser | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (payload: LoginRequest) => Promise<void>
  signup: (payload: SignupRequest) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const userRef = useRef(user)
  useEffect(() => { userRef.current = user }, [user])

  /** 인증 결과를 적용: 토큰 저장 + silent refresh 스케줄링 + 사용자 설정 */
  const applyAuthResult = (result: AuthTokenResponse) => {
    setAuthTokens({
      accessToken: result.access_token,
      refreshToken: result.refresh_token,
    })
    tokenManager.start(result.access_expires_in)
    setUser(result.user)
  }

  /** 모든 인증 상태 정리 (토큰 + 캐시 + 스토어 + 사용자) */
  const clearSession = () => {
    tokenManager.stop()
    clearAuthTokens()
    queryClient.clear()
    useChatStore.getState().switchThread(null)
    setUser(null)
  }

  const refreshUser = async () => {
    const me = await authApi.me()
    setUser(me)
  }

  const login = async (payload: LoginRequest) => {
    const result = await authApi.login(payload)
    applyAuthResult(result)
  }

  const signup = async (payload: SignupRequest) => {
    const result = await authApi.signup(payload)
    applyAuthResult(result)
  }

  const logout = async () => {
    const refreshToken = getRefreshToken() || undefined
    try {
      await authApi.logout(refreshToken)
    } catch {
      // 서버 로그아웃 실패 시에도 클라이언트 상태는 정리
    }
    clearSession()
  }

  // Bootstrap: 앱 시작 시 refresh token으로 세션 복원 + TTL 확보
  useEffect(() => {
    const bootstrap = async () => {
      const refreshToken = getRefreshToken()
      if (!refreshToken) {
        setIsLoading(false)
        return
      }

      try {
        const result = await authApi.refresh(refreshToken)
        applyAuthResult(result)
      } catch {
        clearSession()
      } finally {
        setIsLoading(false)
      }
    }
    void bootstrap()
  }, [])

  // tokenManager 인증 실패 콜백 등록
  useEffect(() => {
    tokenManager.setOnAuthFailure(() => {
      toast.info('세션이 만료되었습니다. 다시 로그인해주세요.')
      clearSession()
    })
    return () => tokenManager.setOnAuthFailure(null)
  }, [])

  // 탭 복귀 시 세션 확인 (ref로 user 참조하여 리스너 재등록 방지)
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'visible' && userRef.current) {
        void tokenManager.refreshNow()
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: !!user,
      isLoading,
      login,
      signup,
      logout,
      refreshUser,
    }),
    [user, isLoading]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}
