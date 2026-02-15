import { httpClient } from '../api/httpClient'

export interface AuthUser {
  id: string
  login_id: string
  name: string | null
  is_active: boolean
  created_at: string
}

export interface AuthTokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  access_expires_in: number
  refresh_expires_in: number
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

export async function signup(data: SignupRequest): Promise<AuthTokenResponse> {
  return httpClient.post<AuthTokenResponse>('/auth/signup', data, { skipAuth: true })
}

export async function login(data: LoginRequest): Promise<AuthTokenResponse> {
  return httpClient.post<AuthTokenResponse>('/auth/login', data, { skipAuth: true })
}

export async function refresh(refreshToken: string): Promise<AuthTokenResponse> {
  return httpClient.post<AuthTokenResponse>('/auth/refresh', { refresh_token: refreshToken }, { skipAuth: true, retryOnAuthFailure: false })
}

export async function me(): Promise<AuthUser> {
  return httpClient.get<AuthUser>('/auth/me')
}

export async function checkLoginId(loginId: string): Promise<LoginIdCheckResponse> {
  const query = new URLSearchParams({ login_id: loginId }).toString()
  return httpClient.get<LoginIdCheckResponse>(`/auth/check-id?${query}`, {
    skipAuth: true,
    retryOnAuthFailure: false,
  })
}

export async function logout(refreshToken?: string): Promise<void> {
  await httpClient.post('/auth/logout', { refresh_token: refreshToken })
}

export const authApi = {
  signup,
  login,
  refresh,
  me,
  checkLoginId,
  logout,
}
