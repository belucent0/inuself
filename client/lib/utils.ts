import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * UTC 시간 문자열을 KST(한국 표준시, UTC+9)로 변환하여 포맷팅합니다.
 * @param utcDateString UTC 시간 문자열 (ISO 8601 형식)
 * @returns KST로 변환된 날짜 문자열 (예: "2024. 01. 01. 12:00:00")
 */
export function formatToKST(utcDateString: string): string {
  const date = new Date(utcDateString)
  
  // Asia/Seoul 시간대를 사용하여 KST로 변환
  return date.toLocaleString('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

