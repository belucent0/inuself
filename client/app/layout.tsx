'use client'

import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import './globals.css'
import { AppSidebar } from '@/components/app-sidebar'
import { SidebarProvider, SidebarInset, SidebarTrigger } from '@/components/ui/sidebar'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { getPageTitle } from '@/lib/navigation'
import { Toaster } from 'sonner'
import { initTelemetry } from '@/lib/telemetry'
import { ThreadTitleProvider, useThreadTitle } from '@/lib/contexts/ThreadTitleContext'
import { MoreVertical, Edit2, Trash2 } from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

// 클라이언트 환경변수에서 관리자 계정 정보 읽기
const ADMIN_USERNAME = process.env.NEXT_PUBLIC_ADMIN_USERNAME || 'admin'
const ADMIN_PASSWORD = process.env.NEXT_PUBLIC_ADMIN_PASSWORD || 'admin123'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isChecking, setIsChecking] = useState(true)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  // 컨텐츠 상세 페이지인지 확인
  const isDetailPage = pathname?.startsWith('/contents/') && pathname !== '/contents'

  useEffect(() => {
    // OpenTelemetry 초기화
    initTelemetry()

    // localStorage에 인증 플래그가 있으면 통과
    const authFlag = localStorage.getItem('admin_auth')
    if (authFlag === 'true') {
      setIsAuthenticated(true)
      setIsChecking(false)
    } else {
      setIsChecking(false)
    }
  }, [])

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    // 클라이언트에서 직접 검증
    if (username === ADMIN_USERNAME && password === ADMIN_PASSWORD) {
      localStorage.setItem('admin_auth', 'true')
      setIsAuthenticated(true)
    } else {
      setError('인증 실패')
    }
  }

  if (isChecking) {
    return (
      <html lang="ko">
        <body>
          <div className="flex items-center justify-center min-h-screen">
            <p className="text-muted-foreground">로딩 중...</p>
          </div>
        </body>
      </html>
    )
  }

  if (!isAuthenticated) {
    return (
      <html lang="ko">
        <body>
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
                  {error && (
                    <p className="text-sm text-destructive">{error}</p>
                  )}
                  <Button type="submit" className="w-full">
                    로그인
                  </Button>
                </form>
              </DialogContent>
            </Dialog>
          </div>
        </body>
      </html>
    )
  }

  return (
    <html lang="ko">
      <body>
        <ThreadTitleProvider>
          <SidebarProvider defaultOpen={!isDetailPage}>
            <AppSidebar />
            <SidebarInset>
              {/* 상단바 - viewport 상단에 고정 */}
              <DynamicHeader pathname={pathname} />
              <main className="flex-1 p-4 md:p-8">
                {children}
              </main>
            </SidebarInset>
          </SidebarProvider>
          <Toaster richColors position="top-center" />
        </ThreadTitleProvider>
      </body>
    </html>
  )
}

// 동적 헤더 컴포넌트 (Context 사용)
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
      {/* 스레드 페이지에서만 메뉴 표시 */}
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
