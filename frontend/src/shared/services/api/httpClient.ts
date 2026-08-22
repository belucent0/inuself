/** Same-origin API client for the browser session cookie. */

export const AUTH_UNAUTHORIZED_EVENT = 'auth:unauthorized'

export interface RequestConfig {
  headers?: Record<string, string>
  signal?: AbortSignal
  reportUnauthorized?: boolean
}

export interface ApiError extends Error {
  status: number
  statusText: string
  data?: unknown
}

export function isApiUnavailable(error: unknown): boolean {
  const status = (error as Partial<ApiError> | null)?.status
  return typeof status !== 'number' || status >= 500
}

export function getBaseUrl(): string {
  return '/api'
}

function resolveUrl(endpoint: string): string {
  return `${getBaseUrl()}${endpoint}`
}

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

function reportUnauthorized(response: Response, config?: RequestConfig): void {
  if (
    response.status === 401 &&
    config?.reportUnauthorized !== false &&
    typeof window !== 'undefined'
  ) {
    window.dispatchEvent(new Event(AUTH_UNAUTHORIZED_EVENT))
  }
}

async function handleResponse<T>(response: Response, config?: RequestConfig): Promise<T> {
  reportUnauthorized(response, config)

  if (!response.ok) {
    let data: unknown
    try {
      data = await response.json()
    } catch {
      // Non-JSON error responses still retain their HTTP status.
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

function createHeaders(config?: RequestConfig, json = true): Headers {
  const headers = new Headers(config?.headers)
  if (json && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return headers
}

async function request<T>(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  endpoint: string,
  data?: unknown,
  config?: RequestConfig
): Promise<T> {
  const response = await fetch(resolveUrl(endpoint), {
    method,
    headers: createHeaders(config),
    body: data === undefined ? undefined : JSON.stringify(data),
    credentials: 'same-origin',
    signal: config?.signal,
  })
  return handleResponse<T>(response, config)
}

export function get<T>(endpoint: string, config?: RequestConfig): Promise<T> {
  return request<T>('GET', endpoint, undefined, config)
}

export async function verifySessionAfterStreamError(): Promise<void> {
  try {
    await get('/auth/me')
  } catch {
    // Only the shared 401 handler changes auth state; 5xx/network failures do not.
  }
}

export function post<T>(endpoint: string, data?: unknown, config?: RequestConfig): Promise<T> {
  return request<T>('POST', endpoint, data, config)
}

export function patch<T>(endpoint: string, data?: unknown, config?: RequestConfig): Promise<T> {
  return request<T>('PATCH', endpoint, data, config)
}

export function del<T>(endpoint: string, data?: unknown, config?: RequestConfig): Promise<T> {
  return request<T>('DELETE', endpoint, data, config)
}

export async function postForm<T>(
  endpoint: string,
  data: FormData,
  config?: RequestConfig
): Promise<T> {
  const response = await fetch(resolveUrl(endpoint), {
    method: 'POST',
    headers: createHeaders(config, false),
    body: data,
    credentials: 'same-origin',
    signal: config?.signal,
  })
  return handleResponse<T>(response, config)
}

export async function postStream(
  endpoint: string,
  data?: unknown,
  config?: RequestConfig
): Promise<ReadableStream<Uint8Array>> {
  const response = await fetch(resolveUrl(endpoint), {
    method: 'POST',
    headers: createHeaders(config),
    body: data === undefined ? undefined : JSON.stringify(data),
    credentials: 'same-origin',
    signal: config?.signal,
  })

  reportUnauthorized(response, config)
  if (!response.ok) {
    let data: unknown
    try {
      data = await response.json()
    } catch {
      // Non-JSON error responses still retain their HTTP status.
    }
    throw createApiError(
      `HTTP Error: ${response.status} ${response.statusText}`,
      response.status,
      response.statusText,
      data
    )
  }
  if (!response.body) {
    throw new Error('Response body is null')
  }
  return response.body
}

export const httpClient = {
  get,
  post,
  patch,
  delete: del,
  postForm,
  postStream,
  verifySessionAfterStreamError,
  getBaseUrl,
}

export default httpClient
