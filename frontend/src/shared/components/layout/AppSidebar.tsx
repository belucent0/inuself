/**
 * AppSidebar - 애플리케이션 사이드바
 */

import { useState, useEffect } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  LogOut,
  Upload,
  Mic,
  Youtube,
  MessageSquare,
  Plus,
  Search,
  Trash2,
} from 'lucide-react'

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarMenuAction,
  SidebarMenuBadge,
  SidebarInput,
} from '@/shared/components/ui/sidebar'
import { Button } from '@/shared/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/shared/components/ui/tooltip'
import { ScrollArea } from '@/shared/components/ui/scroll-area'
import { navigationItems } from '@/shared/config/navigation'
import { YouTubeLinkModal } from './YouTubeLinkModal'
import { StreamingASRModal } from './StreamingASRModal'
import UploadForm from './UploadForm'
import { uploadApi } from '@/shared/services/endpoints/upload'
import { toast } from 'sonner'
import { useThreads } from '@/shared/hooks/useThreads'
import { dispatchContentsRefresh } from '@/shared/hooks/useContents'
import type { Thread } from '@/shared/types'

function groupThreadsByDate(threads: Thread[]) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)
  const lastWeek = new Date(today)
  lastWeek.setDate(lastWeek.getDate() - 7)

  const groups: Record<string, Thread[]> = {
    오늘: [],
    어제: [],
    '이번 주': [],
    이전: [],
  }

  threads.forEach((thread) => {
    const threadDate = new Date(thread.updated_at * 1000)
    if (threadDate >= today) {
      groups['오늘'].push(thread)
    } else if (threadDate >= yesterday) {
      groups['어제'].push(thread)
    } else if (threadDate >= lastWeek) {
      groups['이번 주'].push(thread)
    } else {
      groups['이전'].push(thread)
    }
  })

  return groups
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const location = useLocation()
  const navigate = useNavigate()
  const pathname = location.pathname
  const [youtubeModalOpen, setYoutubeModalOpen] = useState(false)
  const [streamingModalOpen, setStreamingModalOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')

  const { threads, removeThread, loadThreads } = useThreads()

  useEffect(() => {
    if (pathname?.startsWith('/chat/')) {
      loadThreads()
    }
  }, [pathname, loadThreads])

  const handleYouTubeSubmit = async (url: string) => {
    toast.success('YouTube 영상 다운로드가 시작되었습니다', {
      description: '콘텐츠 목록에서 진행 상황을 확인할 수 있습니다.',
    })
    try {
      await uploadApi.uploadYouTubeContent(url)
      dispatchContentsRefresh()
    } catch {
      toast.error('YouTube 다운로드 실패', {
        description: '링크를 확인하고 다시 시도해주세요.',
      })
    }
  }

  const handleFileUploadClick = () => {
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    fileInput?.click()
  }

  const handleLogout = () => {
    if (confirm('로그아웃하시겠습니까?')) {
      localStorage.removeItem('admin_auth')
      window.location.reload()
    }
  }

  const handleDeleteThread = async (threadId: string) => {
    if (confirm('이 대화를 삭제하시겠습니까?')) {
      await removeThread(threadId)
      if (pathname === `/chat/${threadId}`) {
        navigate('/')
      }
    }
  }

  const filteredThreads = threads.filter((t) =>
    t.title.toLowerCase().includes(searchQuery.toLowerCase())
  )
  const groupedThreads = groupThreadsByDate(filteredThreads)

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <div className="p-2">
          <h1 className="text-lg font-semibold group-data-[collapsible=icon]:hidden">
            ASR 파이프라인
          </h1>
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="group-data-[collapsible=icon]:hidden">
            빠른 작업
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton onClick={() => setYoutubeModalOpen(true)}>
                  <Youtube className="text-red-500" />
                  <span className="group-data-[collapsible=icon]:hidden">YouTube 영상 추가</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton onClick={handleFileUploadClick}>
                  <Upload />
                  <span className="group-data-[collapsible=icon]:hidden">파일 업로드</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton onClick={() => setStreamingModalOpen(true)}>
                  <Mic />
                  <span className="group-data-[collapsible=icon]:hidden">실시간 전사</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <div className="hidden">
          <UploadForm />
        </div>

        <SidebarGroup className="pt-0">
          <SidebarGroupLabel className="group-data-[collapsible=icon]:hidden">메뉴</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navigationItems.map((item) => {
                const Icon = item.icon
                const isActive = pathname === item.href || pathname?.startsWith(item.href + '/')
                const isAIMode = item.label === 'AI 채팅'
                return (
                  <SidebarMenuItem key={item.href}>
                    <SidebarMenuButton asChild isActive={isActive}>
                      <Link to={item.href}>
                        <Icon className={isAIMode ? 'text-indigo-500' : ''} />
                        <span className={isAIMode ? 'font-bold bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 bg-clip-text text-transparent' : ''}>
                          {item.label}
                        </span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                )
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup className="pt-0">
          <SidebarGroupLabel className="group-data-[collapsible=icon]:hidden">
            <div className="flex items-center justify-between w-full">
              <span>대화 목록</span>
              <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => navigate('/')}>
                <Plus className="h-4 w-4" />
              </Button>
            </div>
          </SidebarGroupLabel>

          <SidebarGroupContent>
            <div className="group-data-[collapsible=icon]:hidden">
              <div className="relative mb-2 px-2">
                <Search className="absolute left-4 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
                <SidebarInput placeholder="대화 검색..." className="pl-8" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} />
              </div>

              <ScrollArea className="h-[400px]">
                {Object.entries(groupedThreads).map(([group, groupThreads]) =>
                  groupThreads.length > 0 && (
                    <div key={group} className="mb-4">
                      <div className="px-2 text-xs font-semibold text-muted-foreground mb-1">{group}</div>
                      <SidebarMenu>
                        {groupThreads.map((thread) => (
                          <SidebarMenuItem key={thread.thread_id}>
                            <SidebarMenuButton asChild isActive={pathname === `/chat/${thread.thread_id}`}>
                              <Link to={`/chat/${thread.thread_id}`}>
                                <MessageSquare className="h-4 w-4" />
                                <span className="truncate">{thread.title}</span>
                              </Link>
                            </SidebarMenuButton>
                            <SidebarMenuAction showOnHover onClick={() => handleDeleteThread(thread.thread_id)}>
                              <Trash2 className="h-4 w-4" />
                            </SidebarMenuAction>
                          </SidebarMenuItem>
                        ))}
                      </SidebarMenu>
                    </div>
                  )
                )}
              </ScrollArea>
            </div>

            <div className="hidden group-data-[collapsible=icon]:block">
              <SidebarMenu>
                <SidebarMenuItem>
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <SidebarMenuButton onClick={() => navigate('/')}>
                          <MessageSquare />
                          {threads.length > 0 && <SidebarMenuBadge>{threads.length}</SidebarMenuBadge>}
                        </SidebarMenuButton>
                      </TooltipTrigger>
                      <TooltipContent side="right">대화 목록 ({threads.length}개)</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </SidebarMenuItem>
              </SidebarMenu>
            </div>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <div className="p-2 group-data-[collapsible=icon]:hidden">
          <Button onClick={handleLogout} variant="secondary" className="w-full">
            <LogOut className="mr-2 h-4 w-4" />
            로그아웃
          </Button>
        </div>

        <div className="hidden group-data-[collapsible=icon]:block p-2">
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button onClick={handleLogout} variant="secondary" size="icon" className="w-full">
                  <LogOut className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right"><p>로그아웃</p></TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
      </SidebarFooter>

      <SidebarRail />

      <YouTubeLinkModal open={youtubeModalOpen} onOpenChange={setYoutubeModalOpen} onSubmit={handleYouTubeSubmit} />
      <StreamingASRModal open={streamingModalOpen} onOpenChange={setStreamingModalOpen} />
    </Sidebar>
  )
}
