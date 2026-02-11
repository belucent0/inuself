/**
 * 메시지 하단 액션 버튼들 (Perplexity 스타일)
 *
 * - 복사: 답변 내용을 클립보드에 복사
 * - 좋아요/싫어요: 사용자 피드백
 * - 재생성: 답변 재생성
 * - 공유: 공유 링크 복사
 */

import { useState } from 'react'
import { Copy, Check, ThumbsUp, ThumbsDown, RefreshCw, Share2, Loader2 } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { cn } from '@/shared/utils/cn'
import { toast } from 'sonner'

interface MessageActionsProps {
  content: string
  messageId?: string
  threadId?: string
  onRegenerate?: () => void
  isRegenerating?: boolean
  className?: string
}

export function MessageActions({
  content,
  messageId: _messageId,  // 향후 피드백 API에서 사용 예정
  threadId,
  onRegenerate,
  isRegenerating,
  className,
}: MessageActionsProps) {
  const [copied, setCopied] = useState(false)
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      toast.success('답변이 복사되었습니다')
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      toast.error('복사에 실패했습니다')
    }
  }

  const handleFeedback = async (type: 'up' | 'down') => {
    // 이미 같은 피드백이면 취소
    if (feedback === type) {
      setFeedback(null)
      return
    }

    setFeedback(type)

    // TODO: 백엔드 API 호출 (피드백 저장)
    // await threadsApi.sendFeedback(threadId, messageId, type)

    if (type === 'up') {
      toast.success('피드백 감사합니다!')
    } else {
      toast.success('더 나은 답변을 위해 노력하겠습니다')
    }
  }

  const handleShare = async () => {
    if (!threadId) {
      toast.error('공유할 수 없는 메시지입니다')
      return
    }

    const shareUrl = `${window.location.origin}/chat/${threadId}`

    try {
      // Web Share API 지원 여부 확인
      if (navigator.share) {
        await navigator.share({
          title: 'AI 채팅 공유',
          url: shareUrl,
        })
      } else {
        // 지원하지 않으면 클립보드에 복사
        await navigator.clipboard.writeText(shareUrl)
        toast.success('공유 링크가 복사되었습니다')
      }
    } catch (err) {
      // 사용자가 취소한 경우 무시
      if ((err as Error).name !== 'AbortError') {
        toast.error('공유에 실패했습니다')
      }
    }
  }

  const handleRegenerate = () => {
    if (isRegenerating) return
    onRegenerate?.()
  }

  return (
    <div
      className={cn(
        'flex items-center gap-1 pt-2 opacity-0 group-hover:opacity-100 transition-opacity',
        className
      )}
    >
      {/* 복사 버튼 */}
      <Button
        variant="ghost"
        size="sm"
        onClick={handleCopy}
        className="h-8 px-2 text-muted-foreground hover:text-foreground"
        title="답변 복사"
      >
        {copied ? (
          <Check className="h-4 w-4 text-green-500" />
        ) : (
          <Copy className="h-4 w-4" />
        )}
      </Button>

      {/* 좋아요 버튼 */}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => handleFeedback('up')}
        className={cn(
          'h-8 px-2 text-muted-foreground hover:text-foreground',
          feedback === 'up' && 'text-green-500 hover:text-green-600'
        )}
        title="좋아요"
      >
        <ThumbsUp className={cn('h-4 w-4', feedback === 'up' && 'fill-current')} />
      </Button>

      {/* 싫어요 버튼 */}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => handleFeedback('down')}
        className={cn(
          'h-8 px-2 text-muted-foreground hover:text-foreground',
          feedback === 'down' && 'text-red-500 hover:text-red-600'
        )}
        title="싫어요"
      >
        <ThumbsDown className={cn('h-4 w-4', feedback === 'down' && 'fill-current')} />
      </Button>

      {/* 구분선 */}
      <div className="w-px h-4 bg-border mx-1" />

      {/* 재생성 버튼 */}
      {onRegenerate && (
        <Button
          variant="ghost"
          size="sm"
          onClick={handleRegenerate}
          disabled={isRegenerating}
          className="h-8 px-2 text-muted-foreground hover:text-foreground"
          title="답변 재생성"
        >
          {isRegenerating ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
        </Button>
      )}

      {/* 공유 버튼 */}
      {threadId && (
        <Button
          variant="ghost"
          size="sm"
          onClick={handleShare}
          className="h-8 px-2 text-muted-foreground hover:text-foreground"
          title="공유"
        >
          <Share2 className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}
