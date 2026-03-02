/**
 * 채팅 입력 컴포넌트
 */

import { useRef, type ChangeEvent, type KeyboardEvent, type FormEvent } from 'react'
import { Send } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/shared/components/ui/tooltip'
import { cn } from '@/shared/utils/cn'
import { toast } from 'sonner'
import { type AIMode, AI_MODE_CONFIG } from '../types'
import { AIModeSelector } from './AIModeSelector'

interface ChatInputProps {
  input: string
  onInputChange: (value: string) => void
  onSendMessage: (message: string) => void
  isLoading?: boolean
  mode?: AIMode
  onModeChange?: (mode: AIMode) => void
  showModeDescription?: boolean
  className?: string
}

const placeholders: Record<AIMode, string> = {
  auto: '무엇이든 물어보세요...',
  simple: '무엇이든 물어보세요...',
  search: '웹에서 검색할 내용을 입력하세요...',
  rag: '내 문서에서 검색할 내용을 입력하세요...',
  reasoning: '분석이 필요한 질문을 입력하세요...',
  hybrid: '웹과 내 문서에서 통합 검색할 내용을 입력하세요...',
}

export function ChatInput({
  input,
  onInputChange,
  onSendMessage,
  isLoading = false,
  mode = 'search',
  onModeChange,
  showModeDescription = false,
  className,
}: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const currentConfig = AI_MODE_CONFIG[mode]
  const inputValue = input ?? ''
  const isDisabled = !inputValue.trim() || isLoading

  const adjustHeight = () => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = 'auto'
      textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`
    }
  }

  const handleChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    onInputChange(e.target.value)
    adjustHeight()
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleSubmit = (e?: FormEvent) => {
    e?.preventDefault()
    if (!inputValue.trim() || isLoading) return
    onSendMessage(inputValue)
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  return (
    <div className={cn('relative w-full max-w-3xl mx-auto', className)}>
      <div
        className={cn(
          'relative flex flex-col w-full p-3 rounded-xl border transition-all',
          currentConfig.bgColor,
          'border-border/50 focus-within:ring-1 focus-within:border-opacity-50',
          mode === 'search' && 'focus-within:ring-blue-500/50 focus-within:border-blue-500/50',
          mode === 'rag' && 'focus-within:ring-green-500/50 focus-within:border-green-500/50',
          mode === 'reasoning' &&
            'focus-within:ring-purple-500/50 focus-within:border-purple-500/50',
          mode === 'hybrid' && 'focus-within:ring-amber-500/50 focus-within:border-amber-500/50',
          mode === 'simple' && 'focus-within:ring-slate-500/50 focus-within:border-slate-500/50'
        )}
      >
        <textarea
          ref={textareaRef}
          value={inputValue}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholders[mode]}
          disabled={isLoading}
          className="w-full resize-none bg-transparent border-0 focus-visible:ring-0 p-1 min-h-[44px] max-h-[200px] text-sm md:text-base leading-relaxed scrollbar-thin scrollbar-thumb-muted-foreground/20"
          rows={1}
        />

        <div className="flex justify-between items-end mt-2">
          <div className="flex items-center gap-1">
            <AIModeSelector
              mode={mode}
              onModeChange={(newMode) => {
                onModeChange?.(newMode)
                toast.info(`${AI_MODE_CONFIG[newMode].label} 모드`, {
                  description: AI_MODE_CONFIG[newMode].description,
                  duration: 2000,
                })
              }}
              disabled={isLoading}
              compact
            />

          </div>

          <div className="flex items-center gap-2">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    type="button"
                    size="icon"
                    onClick={() => handleSubmit()}
                    disabled={isDisabled}
                    className="h-8 w-8 rounded-full"
                  >
                    <Send className="h-4 w-4" />
                    <span className="sr-only">전송</span>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>전송</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        </div>
      </div>

      {showModeDescription && (
        <div className="text-center mt-2 text-xs text-muted-foreground animate-in fade-in slide-in-from-bottom-2">
          {currentConfig.description}
        </div>
      )}
    </div>
  )
}
