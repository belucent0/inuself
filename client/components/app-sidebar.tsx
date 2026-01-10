"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { LogOut, Upload, Mic } from "lucide-react"

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

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
    const pathname = usePathname()

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

                                return (
                                    <SidebarMenuItem key={item.href}>
                                        <SidebarMenuButton asChild isActive={isActive}>
                                            <Link href={item.href}>
                                                <Icon />
                                                <span>{item.label}</span>
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
        </Sidebar>
    )
}
