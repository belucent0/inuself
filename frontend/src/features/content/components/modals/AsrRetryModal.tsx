/**
 * ASR 재처리 모달
 * - 화자 수 범위 선택 (RadioGroup) — AudioUploadModal과 동일 UX
 * (전사 모드는 컨테이너 단일화로 폐기, accuracyMode는 'speed'로 고정 송신)
 */

import { useState } from 'react'
import { Button } from '@/shared/components/ui/button'
import { Label } from '@/shared/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/shared/components/ui/radio-group'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/shared/components/ui/dialog'
import {
  SPEAKER_RANGE_OPTIONS,
  getSpeakerRange,
  type SpeakerRange,
} from '@/features/upload/types'

export type AccuracyMode = 'speed' | 'accuracy'

export interface AsrRetryOptions {
  accuracyMode: AccuracyMode
  minSpeakers?: number
  maxSpeakers?: number
}

interface AsrRetryModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: (options: AsrRetryOptions) => void
  isLoading?: boolean
}

export function AsrRetryModal({
  open,
  onOpenChange,
  onConfirm,
  isLoading = false,
}: AsrRetryModalProps) {
  const [speakerRange, setSpeakerRange] = useState<SpeakerRange>('auto')

  const handleConfirm = () => {
    const { min, max } = getSpeakerRange(speakerRange)
    onConfirm({
      accuracyMode: 'speed', // ASR 컨테이너 단일화 (whisper-turbo)
      minSpeakers: min,
      maxSpeakers: max,
    })
  }

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setSpeakerRange('auto')
    }
    onOpenChange(newOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>참석자 수</DialogTitle>
          <DialogDescription>
            *실제 발화자를 기준으로 입력하면 인식률이 향상됩니다.
          </DialogDescription>
        </DialogHeader>

        <div className="py-4">
          <RadioGroup
            value={speakerRange || ''}
            onValueChange={(value) => setSpeakerRange(value as SpeakerRange)}
          >
            <div className="grid grid-cols-2 gap-3">
              {SPEAKER_RANGE_OPTIONS.map((option) => (
                <Label
                  key={option.value}
                  htmlFor={`asr-retry-${option.value}`}
                  className="flex items-center space-x-2 rounded-md border border-input bg-background p-3 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                >
                  <RadioGroupItem value={option.value} id={`asr-retry-${option.value}`} />
                  <span className="text-sm font-normal">{option.label}</span>
                </Label>
              ))}
            </div>
          </RadioGroup>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isLoading}
          >
            취소
          </Button>
          <Button type="button" onClick={handleConfirm} disabled={isLoading}>
            {isLoading ? '처리 중...' : '재처리 시작'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
