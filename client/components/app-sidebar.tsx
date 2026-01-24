"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { LogOut, Upload, Mic, Youtube } from "lucide-react"

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
} from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import UploadForm from "./UploadForm"
import { navigationItems } from "@/lib/navigation"
import { YouTubeLinkModal } from "./YouTubeLinkModal"
import { uploadYouTubeContent } from "@/lib/api"
import { toast } from "sonner"
import { useState } from "react"

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
    const pathname = usePathname()
    const [youtubeModalOpen, setYoutubeModalOpen] = useState(false)

    const handleYouTubeSubmit = async (url: string) => {
        await uploadYouTubeContent(url)
        toast.success("YouTube 영상이 처리 대기열에 추가되었습니다", {
            description: "콘텐츠 목록에서 진행 상황을 확인할 수 있습니다.",
        })
    }

    const handleLogout = () => {
        if (confirm("로그아웃하시겠습니까?")) {
            localStorage.removeItem("admin_auth")
            window.location.reload()
        }
    }

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
                {/* 펼쳐진 상태: YouTube 링크 */}
                <SidebarGroup className="group-data-[collapsible=icon]:hidden">
                    <SidebarGroupLabel>YouTube 링크</SidebarGroupLabel>
                    <SidebarGroupContent>
                        <div className="px-2">
                            <Button
                                variant="outline"
                                className="w-full justify-start gap-2"
                                onClick={() => setYoutubeModalOpen(true)}
                            >
                                <Youtube className="h-4 w-4 text-red-500" />
                                YouTube 영상 추가
                            </Button>
                        </div>
                    </SidebarGroupContent>
                </SidebarGroup>

                {/* 펼쳐진 상태: 전체 업로드 폼 표시 */}
                <SidebarGroup className="group-data-[collapsible=icon]:hidden">
                    <SidebarGroupLabel>파일 업로드</SidebarGroupLabel>
                    <SidebarGroupContent>
                        <div className="px-2">
                            <UploadForm />
                        </div>
                    </SidebarGroupContent>
                </SidebarGroup>

                {/* 접힌 상태: 아이콘 버튼만 표시 */}
                <SidebarGroup className="hidden group-data-[collapsible=icon]:block">
                    <SidebarGroupContent>
                        <SidebarMenu>
                            <SidebarMenuItem>
                                <TooltipProvider>
                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <SidebarMenuButton onClick={() => setYoutubeModalOpen(true)}>
                                                <Youtube className="text-red-500" />
                                            </SidebarMenuButton>
                                        </TooltipTrigger>
                                        <TooltipContent side="right">
                                            <p>YouTube 링크</p>
                                        </TooltipContent>
                                    </Tooltip>
                                </TooltipProvider>
                            </SidebarMenuItem>
                            <SidebarMenuItem>
                                <TooltipProvider>
                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <SidebarMenuButton
                                                onClick={() => {
                                                    // 파일 입력 트리거
                                                    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
                                                    fileInput?.click()
                                                }}
                                            >
                                                <Upload />
                                            </SidebarMenuButton>
                                        </TooltipTrigger>
                                        <TooltipContent side="right">
                                            <p>파일 업로드</p>
                                        </TooltipContent>
                                    </Tooltip>
                                </TooltipProvider>
                            </SidebarMenuItem>
                            <SidebarMenuItem>
                                <TooltipProvider>
                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <SidebarMenuButton
                                                onClick={() => {
                                                    // 실시간 전사 모달 열기
                                                    const streamingButton = document.querySelector('[data-streaming-asr-trigger]') as HTMLButtonElement
                                                    streamingButton?.click()
                                                }}
                                            >
                                                <Mic />
                                            </SidebarMenuButton>
                                        </TooltipTrigger>
                                        <TooltipContent side="right">
                                            <p>실시간 전사</p>
                                        </TooltipContent>
                                    </Tooltip>
                                </TooltipProvider>
                            </SidebarMenuItem>
                        </SidebarMenu>
                    </SidebarGroupContent>
                </SidebarGroup>

                <Separator className="my-2" />

                <SidebarGroup>
                    <SidebarGroupLabel className="group-data-[collapsible=icon]:hidden">
                        메뉴
                    </SidebarGroupLabel>
                    <SidebarGroupContent>
                        <SidebarMenu>
                            {navigationItems.map((item) => {
                                const Icon = item.icon
                                const isActive =
                                    pathname === item.href || pathname?.startsWith(item.href + "/")
                                const isAIMode = item.label === "AI 모드"

                                return (
                                    <SidebarMenuItem key={item.href}>
                                        <SidebarMenuButton asChild isActive={isActive}>
                                            <Link href={item.href}>
                                                <Icon className={isAIMode ? "text-indigo-500" : ""} />
                                                <span className={isAIMode ? "font-bold bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500 bg-clip-text text-transparent" : ""}>
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
            </SidebarContent>

            <SidebarFooter>
                {/* 펼쳐진 상태: 전체 버튼 */}
                <div className="p-2 group-data-[collapsible=icon]:hidden">
                    <Button
                        onClick={handleLogout}
                        variant="secondary"
                        className="w-full"
                    >
                        <LogOut className="mr-2 h-4 w-4" />
                        로그아웃
                    </Button>
                </div>

                {/* 접힌 상태: 아이콘 버튼 */}
                <div className="hidden group-data-[collapsible=icon]:block p-2">
                    <TooltipProvider>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    onClick={handleLogout}
                                    variant="secondary"
                                    size="icon"
                                    className="w-full"
                                >
                                    <LogOut className="h-4 w-4" />
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent side="right">
                                <p>로그아웃</p>
                            </TooltipContent>
                        </Tooltip>
                    </TooltipProvider>
                </div>
            </SidebarFooter>

            <SidebarRail />

            <YouTubeLinkModal
                open={youtubeModalOpen}
                onOpenChange={setYoutubeModalOpen}
                onSubmit={handleYouTubeSubmit}
            />
        </Sidebar>
    )
}
