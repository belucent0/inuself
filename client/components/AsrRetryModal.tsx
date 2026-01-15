'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'

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
  const [accuracyMode, setAccuracyMode] = useState<AccuracyMode>('speed')
  const [minSpeakers, setMinSpeakers] = useState<string>('')
  const [maxSpeakers, setMaxSpeakers] = useState<string>('')
  const [error, setError] = useState<string>('')

  const handleConfirm = () => {
    setError('')

    let minSpeakersValue: number | undefined = undefined
    let maxSpeakersValue: number | undefined = undefined

    if (minSpeakers.trim()) {
      const parsed = parseInt(minSpeakers.trim())
      if (isNaN(parsed) || parsed < 1) {
        setError('최소 화자 수는 1 이상의 정수여야 합니다.')
        return
      }
      minSpeakersValue = parsed
    }

    if (maxSpeakers.trim()) {
      const parsed = parseInt(maxSpeakers.trim())
      if (isNaN(parsed) || parsed < 1) {
        setError('최대 화자 수는 1 이상의 정수여야 합니다.')
        return
      }
      maxSpeakersValue = parsed
    }

    if (minSpeakersValue !== undefined && maxSpeakersValue !== undefined && minSpeakersValue > maxSpeakersValue) {
      setError('최소 화자 수는 최대 화자 수보다 작거나 같아야 합니다.')
      return
    }

    onConfirm({
      accuracyMode,
      minSpeakers: minSpeakersValue,
      maxSpeakers: maxSpeakersValue,
    })
  }

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      // 모달 닫힐 때 상태 초기화
      setAccuracyMode('speed')
      setMinSpeakers('')
      setMaxSpeakers('')
      setError('')
    }
    onOpenChange(newOpen)
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>ASR 재처리 옵션</DialogTitle>
          <DialogDescription>
            음성 인식 처리 옵션을 선택하세요.
          </DialogDescription>
        </DialogHeader>

        <div className="py-4 space-y-6">
          {/* 처리 모드 선택 */}
          <div>
            <Label className="text-sm font-medium mb-3 block">처리 모드</Label>
            <RadioGroup
              value={accuracyMode}
              onValueChange={(value) => setAccuracyMode(value as AccuracyMode)}
            >
              <div className="grid grid-cols-2 gap-3">
                <Label
                  htmlFor="asr-modal-speed"
                  className="flex flex-col space-y-1 rounded-md border border-input bg-background p-3 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                >
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="speed" id="asr-modal-speed" />
                    <span className="text-sm font-semibold">신속 모드</span>
                  </div>
                  <p className="text-xs text-muted-foreground ml-6">
                    빠른 처리 (whisper-turbo)
                  </p>
                </Label>
                <Label
                  htmlFor="asr-modal-accuracy"
                  className="flex flex-col space-y-1 rounded-md border border-input bg-background p-3 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                >
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="accuracy" id="asr-modal-accuracy" />
                    <span className="text-sm font-semibold">정확도 모드</span>
                  </div>
                  <p className="text-xs text-muted-foreground ml-6">
                    높은 정확도 (whisper-large-v3)
                  </p>
                </Label>
              </div>
            </RadioGroup>
          </div>

          {/* 화자 수 설정 */}
          <div>
            <Label className="text-sm font-medium mb-3 block">화자 수 (선택사항)</Label>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="asr-modal-min-speakers" className="text-xs text-muted-foreground">
                  최소 화자 수
                </Label>
                <Input
                  id="asr-modal-min-speakers"
                  type="number"
                  min="1"
                  value={minSpeakers}
                  onChange={(e) => setMinSpeakers(e.target.value)}
                  placeholder="자동"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="asr-modal-max-speakers" className="text-xs text-muted-foreground">
                  최대 화자 수
                </Label>
                <Input
                  id="asr-modal-max-speakers"
                  type="number"
                  min="1"
                  value={maxSpeakers}
                  onChange={(e) => setMaxSpeakers(e.target.value)}
                  placeholder="자동"
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              비워두면 자동으로 결정됩니다.
            </p>
          </div>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
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
          <Button
            type="button"
            onClick={handleConfirm}
            disabled={isLoading}
          >
            {isLoading ? '처리 중...' : '재처리 시작'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
