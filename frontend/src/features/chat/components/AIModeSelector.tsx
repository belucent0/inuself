/**
 * AI 모드 선택 컴포넌트
 */

import {
  MessageSquare,
  Globe,
  Database,
  Brain,
  Sparkles,
} from 'lucide-react'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { cn } from '@/shared/utils/cn'
import { type AIMode, AI_MODE_CONFIG, type AIModeConfig } from '../types'

function ModeIcon({ mode }: { mode: AIMode }) {
  switch (mode) {
    case 'simple':
      return <MessageSquare className="h-4 w-4" />
    case 'search':
      return <Globe className="h-4 w-4" />
    case 'rag':
      return <Database className="h-4 w-4" />
    case 'reasoning':
      return <Brain className="h-4 w-4" />
    case 'hybrid':
      return <Sparkles className="h-4 w-4" />
  }
}

interface AIModeSelectorProps {
  mode: AIMode
  onModeChange: (mode: AIMode) => void
  disabled?: boolean
  compact?: boolean
}

export function AIModeSelector({
  mode,
  onModeChange,
  disabled,
  compact = false,
}: AIModeSelectorProps) {
  const currentConfig = AI_MODE_CONFIG[mode]

  return (
    <Select
      value={mode}
      onValueChange={(value) => onModeChange(value as AIMode)}
      disabled={disabled}
    >
      <SelectTrigger
        className={cn(
          'gap-2 transition-all border-0 shadow-none',
          currentConfig.bgColor,
          currentConfig.color,
          'hover:opacity-80 focus:ring-0',
          compact ? 'h-8 w-auto px-2' : 'h-9 w-[160px]'
        )}
      >
        <div className="flex items-center gap-2">
          <span className={currentConfig.color}>
            <ModeIcon mode={mode} />
          </span>
          {!compact && (
            <SelectValue placeholder="모드 선택">{currentConfig.label}</SelectValue>
          )}
        </div>
      </SelectTrigger>
      <SelectContent className="w-[240px]">
        {(Object.entries(AI_MODE_CONFIG) as [AIMode, AIModeConfig][]).map(
          ([key, config]) => (
            <SelectItem
              key={key}
              value={key}
              className={cn('cursor-pointer py-2', mode === key && config.bgColor)}
            >
              <div className="flex items-center gap-3">
                <div className={cn('p-1.5 rounded-md', config.bgColor, config.color)}>
                  <ModeIcon mode={key} />
                </div>
                <div className="flex flex-col">
                  <span className="font-medium">{config.label}</span>
                  <span className="text-xs text-muted-foreground">
                    {config.description}
                  </span>
                </div>
              </div>
            </SelectItem>
          )
        )}
      </SelectContent>
    </Select>
  )
}

export function AIModeBadge({ mode }: { mode: AIMode }) {
  const config = AI_MODE_CONFIG[mode]

  return (
    <div
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium',
        config.bgColor,
        config.color
      )}
    >
      <ModeIcon mode={mode} />
      <span>{config.label}</span>
    </div>
  )
}

export { ModeIcon }
