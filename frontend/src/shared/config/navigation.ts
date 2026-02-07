/**
 * Navigation 설정
 */

import { FileText, Map, Eye, MessageSquare, type LucideIcon } from 'lucide-react'

export interface NavItem {
  href: string
  label: string
  icon: LucideIcon
  title?: string
}

// 메뉴 구조 정의
export const navigationItems: NavItem[] = [
  { href: '/', label: 'AI 채팅', icon: MessageSquare, title: 'AI 채팅' },
  { href: '/contents', label: '콘텐츠', icon: FileText, title: '콘텐츠' },
  { href: '/monitoring', label: '모니터링', icon: Eye, title: '모니터링' },
  { href: '/roadmap', label: '로드맵', icon: Map, title: '로드맵' },
]

// 경로별 페이지 제목 조회
export function getPageTitle(pathname: string | null): string {
  if (!pathname) return 'ASR 파이프라인'

  // AI Chat routes
  if (pathname === '/' || pathname.startsWith('/chat/')) {
    return 'AI 채팅'
  }

  // 정확한 경로 매칭
  const item = navigationItems.find((item) => item.href === pathname)
  if (item) {
    return item.title || item.label
  }

  // /contents/[id] 같은 동적 라우트 처리
  if (pathname.startsWith('/contents/')) {
    return '콘텐츠 상세'
  }

  return 'ASR 파이프라인'
}
