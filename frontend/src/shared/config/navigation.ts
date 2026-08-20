/**
 * Navigation 설정
 */

import { FileText, Eye, MessageSquare, ClipboardList, History, Lightbulb, PlayCircle, type LucideIcon } from 'lucide-react'

export interface NavSubItem {
  href: string
  label: string
  icon?: LucideIcon
}

export interface NavItem {
  href: string
  label: string
  icon: LucideIcon
  title?: string
  subItems?: NavSubItem[]
}

// 메뉴 구조 정의
export const navigationItems: NavItem[] = [
  { href: '/', label: 'AI 채팅', icon: MessageSquare, title: 'AI 채팅' },
  { href: '/threads', label: '대화 기록', icon: History, title: '대화 기록' },
  { href: '/contents', label: '콘텐츠', icon: FileText, title: '콘텐츠' },
  { href: '/insights', label: '영상 인사이트', icon: Lightbulb, title: '영상 인사이트' },
  {
    href: '/scan',
    label: '심리검사',
    icon: ClipboardList,
    title: '심리검사',
    subItems: [
      { href: '/scan', label: '검사하기', icon: PlayCircle },
      { href: '/scan/history', label: '검사 이력', icon: History },
    ],
  },
  { href: '/monitoring', label: '모니터링', icon: Eye, title: '모니터링' },
]

// 경로별 페이지 제목 조회
export function getPageTitle(pathname: string | null): string {
  if (!pathname) return 'InuSelf'

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

  if (pathname.startsWith('/insights/')) {
    return '영상 인사이트'
  }

  // /scan/* 같은 동적 라우트 처리
  if (pathname.startsWith('/scan')) {
    return '심리검사'
  }

  return 'InuSelf'
}
