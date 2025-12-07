'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { FileText, Map, LogOut } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { SheetClose } from '@/components/ui/sheet'
import UploadForm from './UploadForm'

type Props = {
  isOpen?: boolean
  onClose?: () => void
  isMobileSheet?: boolean
}

export default function Sidebar({ isOpen = false, onClose, isMobileSheet = false }: Props) {
  const pathname = usePathname()
  
  const handleLogout = () => {
    if (confirm('로그아웃하시겠습니까?')) {
      localStorage.removeItem('admin_auth')
      window.location.reload()
    }
  }

  const handleLinkClick = () => {
    if (onClose) {
      onClose()
    }
  }

  const navItems = [
    { href: '/contents', label: '콘텐츠', icon: FileText },
    { href: '/roadmap', label: '로드맵', icon: Map },
  ]

  const sidebarContent = (
    <div className="flex flex-col h-full bg-muted/40">
      <div className="p-6 space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-foreground">ASR 파이프라인</h1>
        </div>
        <Separator />
        <UploadForm />
        <Separator />
        <Button
          onClick={handleLogout}
          variant="secondary"
          className="w-full"
        >
          <LogOut className="mr-2 h-4 w-4" />
          로그아웃
        </Button>
      </div>
      <Separator />
      <ScrollArea className="flex-1 px-6">
        <nav className="space-y-1 py-4">
          {navItems.map((item) => {
            const Icon = item.icon
            const isActive = pathname === item.href || pathname?.startsWith(item.href + '/')
            const linkContent = (
              <>
                <Icon className="h-4 w-4" />
                {item.label}
              </>
            )
            
            if (isMobileSheet) {
              return (
                <SheetClose key={item.href} asChild>
                  <Link
                    href={item.href}
                    className={`
                      flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors
                      ${isActive 
                        ? 'bg-primary text-primary-foreground' 
                        : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                      }
                    `}
                  >
                    {linkContent}
                  </Link>
                </SheetClose>
              )
            }
            
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={handleLinkClick}
                className={`
                  flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors
                  ${isActive 
                    ? 'bg-primary text-primary-foreground' 
                    : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                  }
                `}
              >
                {linkContent}
              </Link>
            )
          })}
        </nav>
      </ScrollArea>
    </div>
  )

  // 모바일 Sheet 안에서 사용될 때는 sidebarContent만 반환
  if (isMobileSheet) {
    return sidebarContent
  }

  return (
    <>
      {/* 데스크톱 사이드바 */}
      <aside className="hidden md:flex md:w-64 md:flex-col md:fixed md:inset-y-0 md:z-40 border-r bg-background">
        {sidebarContent}
      </aside>
    </>
  )
}
