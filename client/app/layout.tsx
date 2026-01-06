'use client'

import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import Link from 'next/link'
import { cn } from '@/lib/utils'
import './globals.css'
import { AppSidebar } from '@/components/app-sidebar'
import { SidebarProvider, SidebarInset, SidebarTrigger } from '@/components/ui/sidebar'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import {
  Breadcrumb,
  BreadcrumbList,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/components/ui/breadcrumb'

// 클라이언트 환경변수에서 관리자 계정 정보 읽기
const ADMIN_USERNAME = process.env.NEXT_PUBLIC_ADMIN_USERNAME || 'admin'
const ADMIN_PASSWORD = process.env.NEXT_PUBLIC_ADMIN_PASSWORD || 'admin123'

// 페이지 제목 매핑
const pageTitles: Record<string, string> = {
  '/contents': '콘텐츠',
  '/roadmap': '로드맵',
}

function getPageTitle(pathname: string | null): string {
  if (!pathname) return 'ASR 파이프라인'

  // 정확한 경로 매칭
  if (pageTitles[pathname]) {
    return pageTitles[pathname]
  }

  // /contents/[id] 같은 동적 라우트 처리
  if (pathname.startsWith('/contents/')) {
    return '콘텐츠 상세'
  }

  return 'ASR 파이프라인'
}

function getBreadcrumbItems(pathname: string | null) {
  if (!pathname) return []

  if (pathname === '/contents') {
    return [
      { label: '홈', href: '/' },
      { label: '콘텐츠' },
    ]
  }

  if (pathname.startsWith('/contents/')) {
    return [
      { label: '홈', href: '/' },
      { label: '콘텐츠', href: '/contents' },
      { label: '콘텐츠 상세' },
    ]
  }

  if (pathname === '/roadmap') {
    return [
      { label: '홈', href: '/' },
      { label: '로드맵' },
    ]
  }

  return []
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isChecking, setIsChecking] = useState(true)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  // 컨텐츠 상세 페이지인지 확인
  const isDetailPage = pathname?.startsWith('/contents/') && pathname !== '/contents'
  const breadcrumbItems = getBreadcrumbItems(pathname)

  useEffect(() => {
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
        <SidebarProvider defaultOpen={!isDetailPage}>
          <AppSidebar />
          <SidebarInset>
            <header className="flex h-16 shrink-0 items-center gap-2 border-b px-4">
              <SidebarTrigger className="-ml-1" />
              <Separator orientation="vertical" className="mr-2 h-4" />
              {breadcrumbItems.length > 0 ? (
                <Breadcrumb className="text-base">
                  <BreadcrumbList>
                    {breadcrumbItems.map((item, index) => {
                      const isLast = index === breadcrumbItems.length - 1

                      return (
                        <div key={index} className="flex items-center">
                          <BreadcrumbItem>
                            {isLast ? (
                              <BreadcrumbPage className="font-bold">{item.label}</BreadcrumbPage>
                            ) : item.href ? (
                              <BreadcrumbLink asChild className="font-normal">
                                <Link href={item.href}>{item.label}</Link>
                              </BreadcrumbLink>
                            ) : (
                              <BreadcrumbPage className="font-bold">{item.label}</BreadcrumbPage>
                            )}
                          </BreadcrumbItem>
                          {!isLast && <BreadcrumbSeparator />}
                        </div>
                      )
                    })}
                  </BreadcrumbList>
                </Breadcrumb>
              ) : (
                <h1 className="text-lg font-semibold">
                  {getPageTitle(pathname)}
                </h1>
              )}
            </header>
            <main className="flex-1 p-4 md:p-8">
              {children}
            </main>
          </SidebarInset>
        </SidebarProvider>
      </body>
    </html>
  )
}
