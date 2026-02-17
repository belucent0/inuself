import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { authApi, type AuthUser, type LoginRequest, type SignupRequest } from '@/shared/services/endpoints/auth'
import { clearAuthTokens, getAccessToken, getRefreshToken, setAuthTokens } from '@/shared/services/authToken'

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

  const refreshUser = async () => {
    const me = await authApi.me()
    setUser(me)
  }

  const login = async (payload: LoginRequest) => {
    const result = await authApi.login(payload)
    setAuthTokens({
      accessToken: result.access_token,
      refreshToken: result.refresh_token,
    })
    setUser(result.user)
  }

  const signup = async (payload: SignupRequest) => {
    const result = await authApi.signup(payload)
    setAuthTokens({
      accessToken: result.access_token,
      refreshToken: result.refresh_token,
    })
    setUser(result.user)
  }

  const logout = async () => {
    const refreshToken = getRefreshToken() || undefined
    try {
      await authApi.logout(refreshToken)
    } catch {
      // 서버 로그아웃 실패 시에도 클라이언트 상태는 정리
    }
    clearAuthTokens()
    setUser(null)
  }

  useEffect(() => {
    const bootstrap = async () => {
      const hasAccessToken = !!getAccessToken()
      const hasRefreshToken = !!getRefreshToken()

      if (!hasAccessToken && !hasRefreshToken) {
        setIsLoading(false)
        return
      }

      try {
        await refreshUser()
      } catch {
        clearAuthTokens()
        setUser(null)
      } finally {
        setIsLoading(false)
      }
    }
    void bootstrap()
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
