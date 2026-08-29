/**
 * OCR 재처리 모달
 * - OCR 모드 선택: 이미지 묘사(portray) / 문서 분석(document)
 * (정확도 모드는 OCR 컨테이너 단일화로 폐기, accuracyMode는 'speed'로 고정 송신)
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
import { getFileExtension } from '../../types'

export type OcrMode = 'portray' | 'document'
export type AccuracyMode = 'speed' | 'accuracy'

function isOfficeFile(filename: string): boolean {
  const ext = getFileExtension(filename)
  return ['.doc', '.xls', '.xlsx', '.ppt', '.pptx'].includes(ext)
}

function isPdfFile(filename: string): boolean {
  return getFileExtension(filename) === '.pdf'
}

interface OcrRetryModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  filename: string
  onConfirm: (ocrMode: OcrMode, accuracyMode: AccuracyMode) => void
  isLoading?: boolean
}

export function OcrRetryModal({
  open,
  onOpenChange,
  filename,
  onConfirm,
  isLoading = false,
}: OcrRetryModalProps) {
  const [ocrMode, setOcrMode] = useState<OcrMode | null>(null)

  const handleConfirm = () => {
    if (!ocrMode) return
    onConfirm(ocrMode, 'speed') // OCR 컨테이너 단일화 (dots.ocr)
  }

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setOcrMode(null)
    }
    onOpenChange(newOpen)
  }

  const isPortrayDisabled = isOfficeFile(filename) || isPdfFile(filename)

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>OCR 재처리 옵션</DialogTitle>
          <DialogDescription>
            문서의 특성에 맞는 처리 방식을 선택하세요.
          </DialogDescription>
        </DialogHeader>

        <div className="py-4">
          <RadioGroup
            value={ocrMode || ''}
            onValueChange={(value) => setOcrMode(value as OcrMode)}
          >
            <div className="space-y-3">
              <Label
                htmlFor="ocr-modal-portray"
                className={`flex flex-col space-y-1 rounded-md border border-input bg-background p-4 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary ${
                  isPortrayDisabled ? 'opacity-50 cursor-not-allowed' : ''
                }`}
              >
                <div className="flex items-center space-x-2">
                  <RadioGroupItem
                    value="portray"
                    id="ocr-modal-portray"
                    disabled={isPortrayDisabled}
                  />
                  <span className="text-sm font-semibold">이미지 묘사</span>
                </div>
                <p className="text-xs text-muted-foreground ml-6">
                  전문적인 시각으로 이미지의 대상, 인물, 상황을 분석하고
                  상세하게 묘사합니다. (이미지 파일 전용)
                </p>
              </Label>
              <Label
                htmlFor="ocr-modal-document"
                className="flex flex-col space-y-1 rounded-md border border-input bg-background p-4 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
              >
                <div className="flex items-center space-x-2">
                  <RadioGroupItem value="document" id="ocr-modal-document" />
                  <span className="text-sm font-semibold">문서 분석</span>
                </div>
                <p className="text-xs text-muted-foreground ml-6">
                  Gemma 4 모델을 사용하여 문서의 텍스트와 구조를 심층적으로
                  분석합니다. (일반 문서, 표가 포함된 문서에 권장)
                </p>
              </Label>
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
          <Button
            type="button"
            onClick={handleConfirm}
            disabled={!ocrMode || isLoading}
          >
            {isLoading ? '처리 중...' : '재처리 시작'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
