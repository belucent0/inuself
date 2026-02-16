/**
 * RootLayout - 애플리케이션 루트 레이아웃
 */

import { useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { MoreVertical, Edit2, Trash2 } from 'lucide-react'

import { SidebarProvider, SidebarInset, SidebarTrigger } from '@/shared/components/ui/sidebar'
import { Separator } from '@/shared/components/ui/separator'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/components/ui/dropdown-menu'
import { AppSidebar } from './AppSidebar'
import { ThreadTitleProvider, useThreadTitle } from '@/shared/contexts/ThreadTitleContext'
import { getPageTitle } from '@/shared/config/navigation'
import { cn } from '@/shared/utils/cn'

const ADMIN_USERNAME = import.meta.env.VITE_ADMIN_USERNAME || 'admin'
const ADMIN_PASSWORD = import.meta.env.VITE_ADMIN_PASSWORD || 'admin123'

const LEGACY_ADMIN_USERNAME = 'nature'
const LEGACY_ADMIN_PASSWORD = 'nature'

function isValidAdminCredential(username: string, password: string): boolean {
  if (username === ADMIN_USERNAME && password === ADMIN_PASSWORD) {
    return true
  }

  return username === LEGACY_ADMIN_USERNAME && password === LEGACY_ADMIN_PASSWORD
}

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
  // 특수 페이지들은 자체 스크롤/패딩 관리
  const isSpecialPage = isChatPage || isDetailPage || isWpiTestPage
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
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isChecking, setIsChecking] = useState(true)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    const authFlag = localStorage.getItem('admin_auth')
    if (authFlag === 'true') {
      setIsAuthenticated(true)
    }
    setIsChecking(false)
  }, [])

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (isValidAdminCredential(username, password)) {
      localStorage.setItem('admin_auth', 'true')
      setIsAuthenticated(true)
    } else {
      setError('인증 실패')
    }
  }

  if (isChecking) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <p className="text-muted-foreground">로딩 중...</p>
      </div>
    )
  }

  if (!isAuthenticated) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
        <Dialog open={true}>
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle>관리자 로그인</DialogTitle>
            </DialogHeader>
            <form onSubmit={handleLogin} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="username">계정</Label>
                <Input
                  id="username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  autoFocus
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">비밀번호</Label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </div>
              {error && <p className="text-sm text-destructive">{error}</p>}
              <Button type="submit" className="w-full">
                로그인
              </Button>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    )
  }

  return <AuthenticatedLayout />
}
