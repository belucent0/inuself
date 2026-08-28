/**
 * 문서 파일 업로드 모달
 * - OCR 모드 선택 (이미지 묘사 / 문서 분석)
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
import { type OcrMode, isOfficeDocument } from '../types'

interface DocumentUploadModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  filename: string
  ocrMode: OcrMode
  onOcrModeChange: (mode: OcrMode) => void
  isUploading: boolean
  onUpload: () => void
  onCancel: () => void
}

export function DocumentUploadModal({
  open,
  onOpenChange,
  filename,
  ocrMode,
  onOcrModeChange,
  isUploading,
  onUpload,
  onCancel,
}: DocumentUploadModalProps) {
  const isOffice = isOfficeDocument(filename)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>문서 처리 방식</DialogTitle>
          <DialogDescription>
            *문서의 복잡도에 따라 처리 방식을 선택하세요.
          </DialogDescription>
        </DialogHeader>

        <div className="py-4">
          <RadioGroup
            value={ocrMode || ''}
            onValueChange={(value) => onOcrModeChange(value as OcrMode)}
          >
            <div className="space-y-3">
              <Label
                htmlFor="portray"
                className={`flex flex-col space-y-1 rounded-md border border-input bg-background p-4 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary ${
                  isOffice ? 'opacity-50 cursor-not-allowed' : ''
                }`}
              >
                <div className="flex items-center space-x-2">
                  <RadioGroupItem
                    value="portray"
                    id="portray"
                    disabled={isOffice}
                  />
                  <span className="text-sm font-semibold">이미지 묘사</span>
                </div>
                <p className="text-xs text-muted-foreground ml-6">
                  전문적인 시각으로 이미지의 대상, 인물, 상황을 분석하고 상세하게
                  묘사합니다.
                </p>
              </Label>
              <Label
                htmlFor="document"
                className="flex flex-col space-y-1 rounded-md border border-input bg-background p-4 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
              >
                <div className="flex items-center space-x-2">
                  <RadioGroupItem value="document" id="document" />
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
            onClick={onCancel}
            disabled={isUploading}
          >
            취소
          </Button>
          <Button
            type="button"
            onClick={onUpload}
            disabled={isUploading || !ocrMode}
          >
            {isUploading ? '업로드 중...' : '업로드'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
