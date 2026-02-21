/**
 * RootLayout - 애플리케이션 루트 레이아웃
 */

import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { MoreVertical, Edit2, Trash2 } from 'lucide-react'

import { SidebarProvider, SidebarInset, SidebarTrigger } from '@/shared/components/ui/sidebar'
import { Separator } from '@/shared/components/ui/separator'
import { Button } from '@/shared/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import { AppSidebar } from './AppSidebar'
import { ThreadTitleProvider, useThreadTitle } from '@/shared/contexts/ThreadTitleContext'
import { useAuth } from '@/shared/contexts'
import { getPageTitle } from '@/shared/config/navigation'
import { cn } from '@/shared/utils/cn'

function DynamicHeader({ pathname }: { pathname: string | null }) {
  const { threadTitle, onEditTitle, onDeleteThread } = useThreadTitle()
  const isThreadPage = pathname?.startsWith('/chat/')

  return (
    <header className="sticky top-0 z-40 flex h-16 shrink-0 items-center gap-2 border-b px-4 bg-background">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <h1 className="text-lg font-semibold flex-1">
        {isThreadPage ? threadTitle : getPageTitle(pathname)}
      </h1>
      {isThreadPage && (onEditTitle || onDeleteThread) && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon">
              <MoreVertical className="h-5 w-5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {onEditTitle && (
              <DropdownMenuItem onClick={onEditTitle}>
                <Edit2 className="h-4 w-4 mr-2" />
                제목 변경
              </DropdownMenuItem>
            )}
            {onDeleteThread && (
              <DropdownMenuItem onClick={onDeleteThread}>
                <Trash2 className="h-4 w-4 mr-2" />
                삭제
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </header>
  )
}

function AuthenticatedLayout() {
  const location = useLocation()
  const pathname = location.pathname
  const isDetailPage = pathname?.startsWith('/contents/') && pathname !== '/contents'
  const isChatPage = pathname?.startsWith('/chat/')
  const isWpiTestPage = pathname === '/scan/wpi'
  const isMonitoringPage = pathname?.startsWith('/monitoring')
  // 특수 페이지들은 자체 스크롤/패딩 관리
  const isSpecialPage = isChatPage || isDetailPage || isWpiTestPage || isMonitoringPage
  return (
    <ThreadTitleProvider>
      <SidebarProvider defaultOpen={!isDetailPage}>
        <AppSidebar />
        <SidebarInset>
          <DynamicHeader pathname={pathname} />
          <main className={cn("flex-1", isSpecialPage ? "overflow-hidden" : "overflow-y-auto p-4 md:p-8")}>
            <Outlet />
          </main>
        </SidebarInset>
      </SidebarProvider>
    </ThreadTitleProvider>
  )
}

export function RootLayout() {
  const location = useLocation()
  const { isAuthenticated, isLoading } = useAuth()

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <p className="text-muted-foreground">로딩 중...</p>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <AuthenticatedLayout />
}
