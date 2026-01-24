import { MessageSquare, FileText, Activity, Map, ListTodo, GitBranch, Sparkles } from "lucide-react"
import { LucideIcon } from "lucide-react"

export interface NavItem {
  href: string
  label: string
  icon: LucideIcon
  title?: string // 페이지 제목 (없으면 label 사용)
}

// 메뉴 구조 정의 (단일 소스)
export const navigationItems: NavItem[] = [
  { href: "/", label: "AI 모드", icon: Sparkles, title: "AI 모드" },
  { href: "/contents", label: "콘텐츠", icon: FileText, title: "콘텐츠" },
  { href: "/monitoring", label: "모니터링", icon: Activity, title: "시스템 모니터링" },
  { href: "/queue-monitoring", label: "큐 모니터링", icon: ListTodo, title: "큐 모니터링" },
  { href: "/tracing", label: "분산 추적", icon: GitBranch, title: "분산 추적" },
  { href: "/roadmap", label: "로드맵", icon: Map, title: "로드맵" },
]

// 경로별 페이지 제목 조회
export function getPageTitle(pathname: string | null): string {
  if (!pathname) return "ASR 파이프라인"

  // 정확한 경로 매칭
  const item = navigationItems.find((item) => item.href === pathname)
  if (item) {
    return item.title || item.label
  }

  // /contents/[id] 같은 동적 라우트 처리
  if (pathname.startsWith("/contents/")) {
    return "콘텐츠 상세"
  }

  return "ASR 파이프라인"
}
