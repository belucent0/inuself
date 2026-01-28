/**
 * 에러 코드 및 사용자 메시지 관리.
 *
 * 백엔드와 프론트엔드 간 공유되는 에러 코드 체계입니다.
 * 백엔드: 에러 코드 + 상세 로그 (디버깅용)
 * 프론트엔드: 에러 코드 → 사용자 친화적 메시지 변환
 */

// 에러 코드 정의
export const ErrorCodes = {
  // 연결 관련 (E1xxx)
  E1001: 'E1001',  // AI 서비스 연결 실패
  E1002: 'E1002',  // 서비스 일시 불가
  E1003: 'E1003',  // 요청 타임아웃

  // 처리 관련 (E2xxx)
  E2001: 'E2001',  // 응답 생성 실패
  E2002: 'E2002',  // 검색 실패
  E2003: 'E2003',  // 모델 로드 실패

  // 요청 관련 (E3xxx)
  E3001: 'E3001',  // 요청 한도 초과
  E3002: 'E3002',  // 잘못된 요청

  // 알 수 없음 (E9xxx)
  E9999: 'E9999',  // 정의되지 않은 에러
} as const

export type ErrorCode = typeof ErrorCodes[keyof typeof ErrorCodes]

// 에러 코드별 사용자 메시지
const errorMessages: Record<ErrorCode, string> = {
  [ErrorCodes.E1001]: 'AI 서비스 연결에 실패했습니다.',
  [ErrorCodes.E1002]: '서비스가 일시적으로 사용 불가능합니다.',
  [ErrorCodes.E1003]: '요청 시간이 초과되었습니다.',
  [ErrorCodes.E2001]: '응답 생성 중 오류가 발생했습니다.',
  [ErrorCodes.E2002]: '검색 중 오류가 발생했습니다.',
  [ErrorCodes.E2003]: 'AI 모델 로드에 실패했습니다.',
  [ErrorCodes.E3001]: '요청이 너무 많습니다.',
  [ErrorCodes.E3002]: '잘못된 요청입니다.',
  [ErrorCodes.E9999]: '알 수 없는 오류가 발생했습니다.',
}

// 서버 에러 메시지 → 에러 코드 매핑 (패턴 기반)
const errorPatternMap: Array<{ pattern: RegExp | string; code: ErrorCode }> = [
  { pattern: 'APIConnectionError', code: ErrorCodes.E1001 },
  { pattern: 'connection attempts failed', code: ErrorCodes.E1001 },
  { pattern: 'ServiceUnavailableError', code: ErrorCodes.E1002 },
  { pattern: 'TimeoutError', code: ErrorCodes.E1003 },
  { pattern: 'MidStreamFallbackError', code: ErrorCodes.E2001 },
  { pattern: 'RateLimitError', code: ErrorCodes.E3001 },
]

/**
 * 에러 코드로 사용자 메시지 조회
 */
export function getErrorMessage(code: ErrorCode): string {
  return errorMessages[code] || errorMessages[ErrorCodes.E9999]
}

/**
 * 서버 에러 응답을 사용자 친화적 메시지로 변환.
 *
 * @param errorResponse - 서버 에러 응답 (문자열 또는 {code, message} 객체)
 * @returns 사용자에게 표시할 메시지
 */
export function formatErrorForUser(errorResponse: string | { code?: string; message?: string }): string {
  // 1. 객체 형태인 경우 (에러 코드 포함)
  if (typeof errorResponse === 'object' && errorResponse.code) {
    const code = errorResponse.code as ErrorCode
    const message = errorMessages[code]
    if (message) {
      return `${message} 잠시 후 다시 시도해주세요.`
    }
    // 정의되지 않은 에러 코드
    return `서버 에러 발생(${code}). 잠시 후 다시 시도해주세요.`
  }

  // 2. 문자열 형태인 경우 (레거시 호환)
  const errorStr = typeof errorResponse === 'string'
    ? errorResponse
    : errorResponse.message || ''

  // 패턴 매칭으로 에러 코드 추론
  for (const { pattern, code } of errorPatternMap) {
    const matches = typeof pattern === 'string'
      ? errorStr.includes(pattern)
      : pattern.test(errorStr)

    if (matches) {
      return `${errorMessages[code]} 잠시 후 다시 시도해주세요.`
    }
  }

  // 3. 민감 정보 포함 여부 검사
  const sensitivePatterns = [
    /\/usr\//,
    /\/app\//,
    /Traceback/i,
    /File "[^"]+"/,
    /litellm\./,
    /localhost:\d+/,
  ]

  const containsSensitiveInfo = sensitivePatterns.some(p => p.test(errorStr))

  if (containsSensitiveInfo || errorStr.length > 200) {
    return `서버 에러 발생(${ErrorCodes.E9999}). 잠시 후 다시 시도해주세요.`
  }

  // 4. 짧고 안전한 에러 메시지는 그대로 반환
  return `${errorStr} 잠시 후 다시 시도해주세요.`
}
