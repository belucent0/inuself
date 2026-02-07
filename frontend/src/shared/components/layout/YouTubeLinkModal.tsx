/**
 * YouTube 링크 입력 모달
 */

import { useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Button } from '@/shared/components/ui/button'
import { Loader2, Youtube } from 'lucide-react'

interface YouTubeLinkModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (url: string) => Promise<void>
}

export function YouTubeLinkModal({
  open,
  onOpenChange,
  onSubmit,
}: YouTubeLinkModalProps) {
  const [url, setUrl] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isValidYouTubeUrl = (url: string) => {
    const patterns = [
      /youtube\.com\/watch\?v=/,
      /youtu\.be\//,
      /youtube\.com\/embed\//,
      /youtube\.com\/shorts\//,
    ]
    return patterns.some((p) => p.test(url))
  }

  const handleSubmit = async () => {
    if (!url.trim()) return

    if (!isValidYouTubeUrl(url)) {
      setError('유효한 유튜브 링크를 입력해주세요')
      return
    }

    setIsLoading(true)
    setError(null)

    try {
      await onSubmit(url)
      setUrl('')
      onOpenChange(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : '업로드에 실패했습니다')
    } finally {
      setIsLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !isLoading) {
      handleSubmit()
    }
  }

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setUrl('')
      setError(null)
    }
    onOpenChange(newOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Youtube className="h-5 w-5 text-red-500" />
            YouTube 영상으로 콘텐츠 생성
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 pt-2">
          <Input
            placeholder="https://www.youtube.com/watch?v=..."
            value={url}
            onChange={(e) => {
              setUrl(e.target.value)
              setError(null)
            }}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            className="font-mono text-sm"
          />

          {error && <p className="text-sm text-destructive">{error}</p>}

          <Button
            onClick={handleSubmit}
            disabled={!url.trim() || isLoading}
            className="w-full"
          >
            {isLoading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                처리 중...
              </>
            ) : (
              '콘텐츠 생성 시작'
            )}
          </Button>

          <p className="text-xs text-muted-foreground text-center">
            1시간 이내 영상만 지원됩니다. 영상이 다운로드되고
            <br />
            자동으로 음성 인식 및 요약이 진행됩니다.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  )
}
