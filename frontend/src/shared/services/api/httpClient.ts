/**
 * HTTP Client - 공통 API 요청 처리
 */

import { getAccessToken } from '../authToken'
import { tokenManager } from '../tokenManager'

export interface RequestConfig {
  headers?: Record<string, string>
  timeout?: number
  signal?: AbortSignal
  skipAuth?: boolean
  retryOnAuthFailure?: boolean
}

export interface ApiError extends Error {
  status: number
  statusText: string
  data?: unknown
}

/**
 * 기본 API URL을 가져옵니다
 */
export function getBaseUrl(): string {
  return import.meta.env?.VITE_API_BASE_URL || '/api'
}

function resolveUrl(endpoint: string): string {
  const baseUrl = getBaseUrl()
  return endpoint.startsWith('http') ? endpoint : `${baseUrl}${endpoint}`
}

/**
 * API 에러를 생성합니다
 */
export function createApiError(
  message: string,
  status: number,
  statusText: string,
  data?: unknown
): ApiError {
  const error = new Error(message) as ApiError
  error.status = status
  error.statusText = statusText
  error.data = data
  return error
}

/**
 * 응답을 처리하고 에러가 있으면 throw합니다
 */
async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let data: unknown
    try {
      data = await response.json()
    } catch {
      // JSON 파싱 실패 시 무시
    }
    throw createApiError(
      `HTTP Error: ${response.status} ${response.statusText}`,
      response.status,
      response.statusText,
      data
    )
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json()
}

function createHeaders(config?: RequestConfig): Headers {
  const headers = new Headers({
    'Content-Type': 'application/json',
    ...config?.headers,
  })

  if (!config?.skipAuth) {
    const accessToken = getAccessToken()
    if (accessToken) {
      headers.set('Authorization', `Bearer ${accessToken}`)
    }
  }

  return headers
}

async function request<T>(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  endpoint: string,
  data?: unknown,
  config?: RequestConfig,
  retried = false
): Promise<T> {
  const response = await fetch(resolveUrl(endpoint), {
    method,
    headers: createHeaders(config),
    body: data ? JSON.stringify(data) : undefined,
    signal: config?.signal,
  })

  const shouldRetry =
    response.status === 401 &&
    !retried &&
    !config?.skipAuth &&
    config?.retryOnAuthFailure !== false

  if (shouldRetry) {
    const refreshed = await tokenManager.refreshNow()
    if (refreshed) {
      return request<T>(method, endpoint, data, config, true)
    }
  }

  return handleResponse<T>(response)
}

/**
 * HTTP GET 요청
 */
export async function get<T>(
  endpoint: string,
  config?: RequestConfig
): Promise<T> {
  return request<T>('GET', endpoint, undefined, config)
}

/**
 * HTTP POST 요청
 */
export async function post<T>(
  endpoint: string,
  data?: unknown,
  config?: RequestConfig
): Promise<T> {
  return request<T>('POST', endpoint, data, config)
}

/**
 * HTTP PATCH 요청
 */
export async function patch<T>(
  endpoint: string,
  data?: unknown,
  config?: RequestConfig
): Promise<T> {
  return request<T>('PATCH', endpoint, data, config)
}

/**
 * HTTP DELETE 요청
 */
export async function del<T>(
  endpoint: string,
  data?: unknown,
  config?: RequestConfig
): Promise<T> {
  return request<T>('DELETE', endpoint, data, config)
}

async function requestStream(
  method: 'GET' | 'POST',
  endpoint: string,
  data?: unknown,
  config?: RequestConfig,
  retried = false
): Promise<ReadableStream<Uint8Array>> {
  const response = await fetch(resolveUrl(endpoint), {
    method,
    headers: createHeaders(config),
    body: data ? JSON.stringify(data) : undefined,
    signal: config?.signal,
  })

  if (response.status === 401 && !retried && !config?.skipAuth && config?.retryOnAuthFailure !== false) {
    try {
      await response.body?.cancel()
    } catch {
      // 이미 종료된 오류 응답
    }
    const refreshed = await tokenManager.refreshNow()
    if (refreshed) {
      return requestStream(method, endpoint, data, config, true)
    }
  }

  if (!response.ok) {
    let errorData: unknown
    try {
      errorData = await response.json()
    } catch {
      // JSON 파싱 실패 시 무시
    }
    throw createApiError(
      `HTTP Error: ${response.status} ${response.statusText}`,
      response.status,
      response.statusText,
      errorData
    )
  }

  if (!response.body) {
    throw new Error('Response body is null')
  }

  return response.body
}

/**
 * SSE GET 스트리밍 요청
 */
export async function getStream(
  endpoint: string,
  config?: RequestConfig
): Promise<ReadableStream<Uint8Array>> {
  return requestStream('GET', endpoint, undefined, config)
}

/**
 * SSE POST 스트리밍 요청
 */
export async function postStream(
  endpoint: string,
  data?: unknown,
  config?: RequestConfig
): Promise<ReadableStream<Uint8Array>> {
  return requestStream('POST', endpoint, data, config)
}

/**
 * HTTP Client 객체
 */
export const httpClient = {
  get,
  post,
  patch,
  delete: del,
  getStream,
  postStream,
  getBaseUrl,
}

export default httpClient
