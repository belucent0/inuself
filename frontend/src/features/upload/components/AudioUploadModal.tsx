/**
 * 오디오 파일 업로드 모달
 * - 화자 수 선택
 * (전사 모드는 컨테이너 단일화로 폐기, accuracyMode prop은 호환만 유지)
 */

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/shared/components/ui/dialog'
import { Button } from '@/shared/components/ui/button'
import { Label } from '@/shared/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/shared/components/ui/radio-group'
import { type SpeakerRange, SPEAKER_RANGE_OPTIONS } from '../types'

interface AudioUploadModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  speakerRange: SpeakerRange
  onSpeakerRangeChange: (range: SpeakerRange) => void
  isUploading: boolean
  onUpload: () => void
  onCancel: () => void
}

export function AudioUploadModal({
  open,
  onOpenChange,
  speakerRange,
  onSpeakerRangeChange,
  isUploading,
  onUpload,
  onCancel,
}: AudioUploadModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
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
            onValueChange={(value) => onSpeakerRangeChange(value as SpeakerRange)}
          >
            <div className="grid grid-cols-2 gap-3">
              {SPEAKER_RANGE_OPTIONS.map((option) => (
                <Label
                  key={option.value}
                  htmlFor={option.value}
                  className="flex items-center space-x-2 rounded-md border border-input bg-background p-3 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                >
                  <RadioGroupItem value={option.value} id={option.value} />
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
            onClick={onCancel}
            disabled={isUploading}
          >
            취소
          </Button>
          <Button type="button" onClick={onUpload} disabled={isUploading}>
            {isUploading ? '업로드 중...' : '업로드'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
