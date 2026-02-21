/**
 * ContentBanner - 독립 스레드에서 연결된 콘텐츠를 표시하는 배너
 * content_id가 연결된 스레드를 /chat/:threadId 로 열 때 표시됨
 */

import { useNavigate } from 'react-router-dom'
import { FileText, ExternalLink } from 'lucide-react'
import { Switch } from '@/shared/components/ui/switch'
import { Label } from '@/shared/components/ui/label'
import { Button } from '@/shared/components/ui/button'
import { useContent } from '@/shared/hooks/useContents'

interface ContentBannerProps {
  contentId: string
  contextEnabled: boolean
  onContextToggle: (enabled: boolean) => void
}

export function ContentBanner({
  contentId,
  contextEnabled,
  onContextToggle,
}: ContentBannerProps) {
  const navigate = useNavigate()
  const { content, isLoading } = useContent(contentId)

  if (isLoading || !content) return null

  const displayTitle = content.title || content.filename

  return (
    <div className="flex items-center gap-3 px-4 py-2 border-b bg-muted/40 text-sm shrink-0">
      <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
      <span className="flex-1 truncate text-muted-foreground">
        연결된 콘텐츠:{' '}
        <span className="font-medium text-foreground">{displayTitle}</span>
      </span>
      <div className="flex items-center gap-2 shrink-0">
        <Switch
          id="content-ctx-toggle"
          checked={contextEnabled}
          onCheckedChange={onContextToggle}
          className="scale-75"
        />
        <Label
          htmlFor="content-ctx-toggle"
          className="text-xs cursor-pointer text-muted-foreground"
        >
          컨텍스트
        </Label>
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="h-6 px-2 text-xs gap-1"
        onClick={() => navigate(`/contents/${contentId}`)}
      >
        <ExternalLink className="h-3 w-3" />
        보기
      </Button>
    </div>
  )
}
