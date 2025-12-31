'use client'

import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import { Menu } from 'lucide-react'
import './globals.css'
import Sidebar from '@/components/Sidebar'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'

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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isChecking, setIsChecking] = useState(true)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false)

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
        <div className="flex min-h-screen">
          {/* 데스크톱 사이드바 */}
          <Sidebar />

          {/* 모바일 헤더 */}
          <header className="md:hidden fixed top-0 left-0 right-0 z-40 h-14 border-b bg-background flex items-center gap-3 px-4">
            <Sheet open={isMobileMenuOpen} onOpenChange={setIsMobileMenuOpen}>
              <SheetContent
                side="left"
                className="w-3/4 max-w-sm p-0 h-full"
                onOpenAutoFocus={(e) => e.preventDefault()}
              >
                <SheetTitle className="sr-only">메뉴</SheetTitle>
                <Sidebar isMobileSheet />
              </SheetContent>
            </Sheet>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
              aria-label="메뉴 토글"
            >
              <Menu className="h-5 w-5" />
            </Button>
            <h1 className="text-lg font-semibold flex-1 truncate">
              {getPageTitle(pathname)}
            </h1>
          </header>

          {/* 메인 콘텐츠 */}
          <main className="flex-1 md:ml-64 pt-14 md:pt-0 p-4 md:p-8">
            {children}
          </main>
        </div>
      </body>
    </html>
  )
}
