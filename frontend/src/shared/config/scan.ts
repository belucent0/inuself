/**
 * 검사 유형별 표시명 설정
 */

export const SCAN_TYPE_DISPLAY_NAMES: Record<string, string> = {
  wpi: "WPI 현실",
  wsi: "WPI 이상",
  mcdc: "MCDC",
} as const

/**
 * scan_type을 표시명으로 변환
 */
export function getScanTypeDisplayName(scanType: string | undefined): string {
  if (!scanType) return "검사"
  return SCAN_TYPE_DISPLAY_NAMES[scanType] || scanType.toUpperCase()
}
