'use client'

import Link from 'next/link'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { FileText, Music, CheckCircle2, XCircle, Image as ImageIcon } from 'lucide-react'
import { Spinner } from '@/components/ui/spinner'
// FileProgressIndicator import 삭제
// useFileProgress 관련 import 삭제
import { FileProgress, FileStatus } from '@/types/file-progress'
import { ContentSummary, ContentStatus } from '@/lib/api'
import { formatToKST } from '@/lib/utils'



const statusLabels: Record<ContentStatus, string> = {
    QUEUED: '대기중',
    PROCESSING: '인식중',
    OCR_PROCESSING: '인식중',
    SUMMARY_QUEUED: '요약 대기',
    SUMMARIZING: '요약중',
    COMPLETED: '완료',
    ASR_FAILED: 'ASR 실패',
    OCR_FAILED: 'OCR 실패',
    SUMMARY_FAILED: '요약 실패',
    CANCELLED: '취소됨',
}

const getStatusVariant = (status: ContentStatus): 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info' => {
    switch (status) {
        case 'COMPLETED':
            return 'success'
        case 'ASR_FAILED':
            return 'destructive'
        case 'OCR_FAILED':
            return 'destructive'
        case 'SUMMARY_FAILED':
            return 'warning'
        case 'PROCESSING':
        case 'OCR_PROCESSING':
        case 'SUMMARIZING':
            return 'info'
        case 'QUEUED':
        case 'SUMMARY_QUEUED':
        case 'CANCELLED':
            return 'outline'
        default:
            return 'outline'
    }
}

const getStatusIcon = (status: ContentStatus) => {
    switch (status) {
        case 'QUEUED':
        case 'PROCESSING':
        case 'OCR_PROCESSING':
        case 'SUMMARY_QUEUED':
        case 'SUMMARIZING':
            return <Spinner className="size-3" />
        case 'COMPLETED':
            return <CheckCircle2 className="size-3" />
        case 'ASR_FAILED':
        case 'OCR_FAILED':
        case 'SUMMARY_FAILED':
        case 'CANCELLED':
            return <XCircle className="size-3" />
        default:
            return null
    }
}

const getFileExtension = (filename: string): string => {
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
    return ext || ''
}

interface ContentItemProps {
    item: ContentSummary
    selected: boolean
    onToggle: (id: number) => void
    onRetry: (id: number, type: 'asr' | 'summary', event: React.MouseEvent) => void
    liveProgress?: FileProgress
}

export function ContentItem({ item, selected, onToggle, onRetry, liveProgress }: ContentItemProps) {
    // 기본 상태 (Props가 없을 때 사용)
    const defaultProgress: FileProgress = {
        fileId: item.id,
        status: 'queued', // 초기값, 아래에서 덮어씀
        step: null,
        progress: 0,
        message: '',
        lastUpdate: null,
        isConnected: false,
    }

    // liveProgress가 있으면 사용, 없으면 기본값
    // 단, liveProgress가 없더라도 item.status를 기반으로 초기 상태 구성 가능
    const progress = liveProgress || defaultProgress

    // 표시할 상태 결정: 소켓 연결되어 있으면 최신 상태(WS), 아니면 DB 상태(API)
    let displayStatus: ContentStatus = item.status

    // WebSocket 상태가 있고 연결되어 있다면 대문자로 변환하여 사용
    if (progress.isConnected && progress.status) {
        // useFileProgress는 'processing', 'queued' (소문자) 반환
        // ContentStatus는 'PROCESSING', 'QUEUED' (대문자)
        let wsStatusUpper = progress.status.toUpperCase() as string

        // 소켓에서 'FAILED'가 오면 콘텐츠 타입에 따라 구체적인 실패 상태로 매핑
        if (wsStatusUpper === 'FAILED') {
            if (item.content_type === 'DOCUMENT') {
                wsStatusUpper = 'OCR_FAILED'
            } else {
                // 오디오/비디오는 ASR_FAILED (요약 실패는 별도 처리가 필요할 수 있으나, 보통 워커 레벨 실패는 ASR/OCR 단계)
                wsStatusUpper = 'ASR_FAILED'
            }
        }

        // 유효한 상태인지 확인 (statusLabels 키에 존재하는지)
        // @ts-ignore
        if (wsStatusUpper in statusLabels) {
            displayStatus = wsStatusUpper as ContentStatus
        }
    }

    return (
        <Card className="hover:shadow-md transition-shadow">
            <CardHeader className="pb-2.5 md:pb-3 px-4 md:px-6 pt-4 md:pt-6">
                <div className="flex items-start gap-2.5 md:gap-3">
                    <Checkbox
                        checked={selected}
                        onCheckedChange={() => onToggle(item.id)}
                        onClick={(e) => e.stopPropagation()}
                        className="mt-0.5 md:mt-1"
                    />
                    <div className="flex-1 min-w-0">
                        <Link href={`/contents/${item.id}`} className="block">
                            <div className="flex items-center gap-2 mb-1.5 md:mb-2">
                                {item.content_type === 'DOCUMENT' ? (
                                    <FileText className="h-4 w-4 md:h-5 md:w-5 text-muted-foreground flex-shrink-0" />
                                ) : item.content_type === 'PORTRAY' ? (
                                    <ImageIcon className="h-4 w-4 md:h-5 md:w-5 text-muted-foreground flex-shrink-0" />
                                ) : item.content_type === 'AUDIO' ? (
                                    <Music className="h-4 w-4 md:h-5 md:w-5 text-muted-foreground flex-shrink-0" />
                                ) : null}
                                <CardTitle className="text-[15px] md:text-lg break-words leading-snug">
                                    {item.title || item.filename}
                                </CardTitle>
                            </div>
                            <div className="flex items-center gap-1.5 md:gap-2 flex-wrap">
                                <Badge variant={getStatusVariant(displayStatus)} className="text-xs flex items-center gap-1.5">
                                    {getStatusIcon(displayStatus)}
                                    {statusLabels[displayStatus]}
                                </Badge>
                                {getFileExtension(item.filename) && (
                                    <Badge variant="outline" className="text-xs">
                                        {getFileExtension(item.filename)}
                                    </Badge>
                                )}
                                {item.content_type !== 'DOCUMENT' && item.content_type !== 'PORTRAY' && (
                                    <span className="text-[13px] md:text-sm text-muted-foreground">
                                        화자 수: {item.speakers.length || 0} · 재생 길이: {item.duration_seconds.toFixed(1)}초
                                    </span>
                                )}
                            </div>
                            <p className="text-[11px] md:text-xs text-muted-foreground mt-1.5 md:mt-2">
                                {formatToKST(item.created_at)}
                            </p>
                        </Link>


                    </div>
                </div>
            </CardHeader>
            {(displayStatus === 'ASR_FAILED' || displayStatus === 'SUMMARY_FAILED') && (
                <CardContent className="pt-0 px-4 md:px-6 pb-3 md:pb-6">
                    <Button
                        type="button"
                        variant={displayStatus === 'ASR_FAILED' ? 'default' : 'secondary'}
                        onClick={(e) => onRetry(item.id, displayStatus === 'ASR_FAILED' ? 'asr' : 'summary', e)}
                        className="w-full h-8 md:h-10 text-xs md:text-sm"
                    >
                        {displayStatus === 'ASR_FAILED' ? 'ASR 재처리' : '요약 재처리'}
                    </Button>
                </CardContent>
            )}
        </Card>
    )
}
