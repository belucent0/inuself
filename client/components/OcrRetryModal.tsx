'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
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

export type OcrMode = 'portray' | 'document'
export type AccuracyMode = 'speed' | 'accuracy'

// 파일 확장자 확인 헬퍼 함수
function getFileExtension(filename: string): string {
  return filename.toLowerCase().substring(filename.lastIndexOf('.'))
}

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
  const [accuracyMode, setAccuracyMode] = useState<AccuracyMode>('speed')

  const handleConfirm = () => {
    if (!ocrMode) return
    onConfirm(ocrMode, accuracyMode)
  }

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      // 모달 닫힐 때 상태 초기화
      setOcrMode(null)
      setAccuracyMode('speed')
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
                  전문적인 시각으로 이미지의 대상, 인물, 상황을 분석하고 상세하게 묘사합니다. (이미지 파일 전용)
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
                  Qwen3-VL 모델을 사용하여 문서의 텍스트와 구조를 심층적으로 분석합니다.
                  (일반 문서, 표가 포함된 문서에 권장)
                </p>
              </Label>
            </div>
          </RadioGroup>

          <div className="mt-6">
            <Label className="text-sm font-medium mb-3 block">처리 모드</Label>
            <RadioGroup
              value={accuracyMode}
              onValueChange={(value) => setAccuracyMode(value as AccuracyMode)}
            >
              <div className="grid grid-cols-2 gap-3">
                <Label
                  htmlFor="ocr-modal-speed"
                  className="flex flex-col space-y-1 rounded-md border border-input bg-background p-3 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                >
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="speed" id="ocr-modal-speed" />
                    <span className="text-sm font-semibold">신속 모드</span>
                  </div>
                  <p className="text-xs text-muted-foreground ml-6">
                    빠른 처리
                  </p>
                </Label>
                <Label
                  htmlFor="ocr-modal-accuracy"
                  className="flex flex-col space-y-1 rounded-md border border-input bg-background p-3 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                >
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value="accuracy" id="ocr-modal-accuracy" />
                    <span className="text-sm font-semibold">정확도 모드</span>
                  </div>
                  <p className="text-xs text-muted-foreground ml-6">
                    높은 정확도
                  </p>
                </Label>
              </div>
            </RadioGroup>
          </div>
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
