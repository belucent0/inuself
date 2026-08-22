import { httpClient } from '../api/httpClient'

export interface AuthUser {
  id: string
  login_id: string
  name: string | null
  is_active: boolean
  is_super: boolean
  created_at: string
}

export interface AuthSessionResponse {
  user: AuthUser
}

export interface SignupRequest {
  login_id: string
  password: string
  signup_code: string
  name?: string
}

export interface LoginRequest {
  login_id: string
  password: string
}

export interface LoginIdCheckResponse {
  login_id: string
  available: boolean
}

export function signup(data: SignupRequest): Promise<AuthSessionResponse> {
  return httpClient.post<AuthSessionResponse>('/auth/signup', data, { reportUnauthorized: false })
}

export function login(data: LoginRequest): Promise<AuthSessionResponse> {
  return httpClient.post<AuthSessionResponse>('/auth/login', data, { reportUnauthorized: false })
}

export function me(reportUnauthorized = true): Promise<AuthUser> {
  return httpClient.get<AuthUser>('/auth/me', { reportUnauthorized })
}

export function checkLoginId(loginId: string): Promise<LoginIdCheckResponse> {
  const query = new URLSearchParams({ login_id: loginId }).toString()
  return httpClient.get<LoginIdCheckResponse>(`/auth/check-id?${query}`, {
    reportUnauthorized: false,
  })
}

export function logout(): Promise<void> {
  return httpClient.post<void>('/auth/logout', undefined, { reportUnauthorized: false })
}

export function logoutAll(): Promise<void> {
  return httpClient.post<void>('/auth/logout-all', undefined, { reportUnauthorized: false })
}

export const authApi = {
  signup,
  login,
  me,
  checkLoginId,
  logout,
  logoutAll,
}
