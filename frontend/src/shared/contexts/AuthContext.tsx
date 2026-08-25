import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  authApi,
  type AuthUser,
  type LoginRequest,
  type SignupRequest,
} from '@/shared/services/endpoints/auth'
import { AUTH_UNAUTHORIZED_EVENT, type ApiError } from '@/shared/services/api/httpClient'
import { queryClient } from '@/shared/lib/queryClient'
import { useChatStore } from '@/shared/stores/chatStore'

type AuthStatus = 'loading' | 'authenticated' | 'anonymous' | 'unavailable'

interface AuthContextValue {
  user: AuthUser | null
  isAuthenticated: boolean
  isLoading: boolean
  isUnavailable: boolean
  login: (payload: LoginRequest) => Promise<void>
  signup: (payload: SignupRequest) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
  retryAuth: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [status, setStatus] = useState<AuthStatus>('loading')
  const userRef = useRef<AuthUser | null>(null)
  const unauthorizedHandledRef = useRef(false)

  const setAuthenticatedUser = useCallback((nextUser: AuthUser) => {
    userRef.current = nextUser
    unauthorizedHandledRef.current = false
    setUser(nextUser)
    setStatus('authenticated')
  }, [])

  const clearSession = useCallback(() => {
    queryClient.clear()
    useChatStore.getState().switchThread(null)
    userRef.current = null
    setUser(null)
    setStatus('anonymous')
  }, [])

  const bootstrap = useCallback(async () => {
    try {
      setAuthenticatedUser(await authApi.me(false))
    } catch (error) {
      if ((error as ApiError)?.status === 401) {
        clearSession()
      } else {
        setStatus('unavailable')
      }
    }
  }, [clearSession, setAuthenticatedUser])

  const refreshUser = useCallback(async () => {
    setAuthenticatedUser(await authApi.me())
  }, [setAuthenticatedUser])

  const login = useCallback(async (payload: LoginRequest) => {
    const result = await authApi.login(payload)
    setAuthenticatedUser(result.user)
  }, [setAuthenticatedUser])

  const signup = useCallback(async (payload: SignupRequest) => {
    const result = await authApi.signup(payload)
    setAuthenticatedUser(result.user)
  }, [setAuthenticatedUser])

  const logout = useCallback(async () => {
    await authApi.logout()
    clearSession()
  }, [clearSession])

  const retryAuth = useCallback(async () => {
    setStatus('loading')
    await bootstrap()
  }, [bootstrap])

  useEffect(() => {
    try {
      localStorage.removeItem('auth_access_token')
      localStorage.removeItem('auth_refresh_token')
    } catch {
      // Storage can be unavailable in hardened browser contexts.
    }
    queueMicrotask(() => { void bootstrap() })
  }, [bootstrap])

  useEffect(() => {
    const handleUnauthorized = () => {
      if (!userRef.current || unauthorizedHandledRef.current) return
      unauthorizedHandledRef.current = true
      clearSession()
      toast.info('세션이 만료되었습니다. 다시 로그인해 주세요.')
    }
    window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized)
    return () => window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized)
  }, [clearSession])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: status === 'authenticated',
      isLoading: status === 'loading',
      isUnavailable: status === 'unavailable',
      login,
      signup,
      logout,
      refreshUser,
      retryAuth,
    }),
    [login, logout, refreshUser, retryAuth, signup, status, user]
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
