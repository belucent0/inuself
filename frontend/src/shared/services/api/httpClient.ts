/**
 * HTTP Client - 공통 API 요청 처리
 *
 * SOLID 원칙:
 * - Single Responsibility: HTTP 요청/응답 처리만 담당
 * - Open/Closed: 새로운 HTTP 메서드 추가 시 확장 가능
 * - Dependency Inversion: 구체적인 fetch 대신 추상화된 인터페이스
 */

export interface RequestConfig {
  headers?: Record<string, string>
  timeout?: number
  signal?: AbortSignal
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
  return import.meta.env.VITE_API_BASE_URL || '/api'
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

  // 204 No Content 처리
  if (response.status === 204) {
    return undefined as T
  }

  return response.json()
}

/**
 * 기본 헤더를 생성합니다
 */
function createHeaders(customHeaders?: Record<string, string>): Headers {
  const headers = new Headers({
    'Content-Type': 'application/json',
    ...customHeaders,
  })
  return headers
}

/**
 * HTTP GET 요청
 */
export async function get<T>(
  endpoint: string,
  config?: RequestConfig
): Promise<T> {
  const baseUrl = getBaseUrl()
  const url = endpoint.startsWith('http') ? endpoint : `${baseUrl}${endpoint}`

  const response = await fetch(url, {
    method: 'GET',
    headers: createHeaders(config?.headers),
    signal: config?.signal,
  })

  return handleResponse<T>(response)
}

/**
 * HTTP POST 요청
 */
export async function post<T>(
  endpoint: string,
  data?: unknown,
  config?: RequestConfig
): Promise<T> {
  const baseUrl = getBaseUrl()
  const url = endpoint.startsWith('http') ? endpoint : `${baseUrl}${endpoint}`

  const response = await fetch(url, {
    method: 'POST',
    headers: createHeaders(config?.headers),
    body: data ? JSON.stringify(data) : undefined,
    signal: config?.signal,
  })

  return handleResponse<T>(response)
}

/**
 * HTTP PATCH 요청
 */
export async function patch<T>(
  endpoint: string,
  data?: unknown,
  config?: RequestConfig
): Promise<T> {
  const baseUrl = getBaseUrl()
  const url = endpoint.startsWith('http') ? endpoint : `${baseUrl}${endpoint}`

  const response = await fetch(url, {
    method: 'PATCH',
    headers: createHeaders(config?.headers),
    body: data ? JSON.stringify(data) : undefined,
    signal: config?.signal,
  })

  return handleResponse<T>(response)
}

/**
 * HTTP DELETE 요청
 */
export async function del<T>(
  endpoint: string,
  data?: unknown,
  config?: RequestConfig
): Promise<T> {
  const baseUrl = getBaseUrl()
  const url = endpoint.startsWith('http') ? endpoint : `${baseUrl}${endpoint}`

  const response = await fetch(url, {
    method: 'DELETE',
    headers: createHeaders(config?.headers),
    body: data ? JSON.stringify(data) : undefined,
    signal: config?.signal,
  })

  return handleResponse<T>(response)
}

/**
 * SSE 스트리밍 요청
 * @returns ReadableStream for SSE events
 */
export async function postStream(
  endpoint: string,
  data?: unknown,
  config?: RequestConfig
): Promise<ReadableStream<Uint8Array>> {
  const baseUrl = getBaseUrl()
  const url = endpoint.startsWith('http') ? endpoint : `${baseUrl}${endpoint}`

  const response = await fetch(url, {
    method: 'POST',
    headers: createHeaders(config?.headers),
    body: data ? JSON.stringify(data) : undefined,
    signal: config?.signal,
  })

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
 * HTTP Client 객체
 */
export const httpClient = {
  get,
  post,
  patch,
  delete: del,
  postStream,
  getBaseUrl,
}

export default httpClient
