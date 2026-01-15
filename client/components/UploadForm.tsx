"use client"

import { useState, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { uploadContent } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { toast } from 'sonner'

type SpeakerRange = 'auto' | '1-2' | '3-6' | '7-10' | '11+' | null
type OcrMode = 'portray' | 'document' | null
type AccuracyMode = 'speed' | 'accuracy'
type OcrAccuracyMode = 'speed' | 'accuracy'
import { StreamingASRModal } from './StreamingASRModal'

export default function UploadForm() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [status, setStatus] = useState<string>('')
  const [isUploading, setUploading] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const [showOcrModal, setShowOcrModal] = useState(false)
  const [showStreamingModal, setShowStreamingModal] = useState(false)
  const [speakerRange, setSpeakerRange] = useState<SpeakerRange>('auto')
  const [accuracyMode, setAccuracyMode] = useState<AccuracyMode>('speed')
  const [ocrMode, setOcrMode] = useState<OcrMode>(null)
  const [ocrAccuracyMode, setOcrAccuracyMode] = useState<OcrAccuracyMode>('speed')
  const router = useRouter()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const isAudioFile = (filename: string): boolean => {
    const audioExtensions = ['.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.wma', '.mp4', '.avi', '.mkv', '.mov', '.webm']
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
    return audioExtensions.includes(ext)
  }

  const isDocumentFile = (filename: string): boolean => {
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
    // 허용된 문서 파일: 이미지 파일과 txt
    let allowedDocumentExtensions = ['.txt', '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp',]
    allowedDocumentExtensions.push(...['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'])
    return allowedDocumentExtensions.includes(ext)
  }

  const isOfficeDocument = (filename: string): boolean => {
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
    const officeExtensions = ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']
    return officeExtensions.includes(ext)
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null
    if (file) {

      // 허용되지 않은 파일 체크
      if (!isAudioFile(file.name) && !isDocumentFile(file.name)) {
        setStatus('지원하지 않는 파일 형식입니다. 오디오/비디오 파일, 이미지 파일, 텍스트 파일, PDF, 또는 Office 문서 파일만 업로드 가능합니다.')
        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
        return
      }

      setSelectedFile(file)
      // 오디오 파일인 경우 화자 수 선택 모달 표시
      if (isAudioFile(file.name)) {
        setShowModal(true)
        setSpeakerRange('auto')
      } else if (isDocumentFile(file.name)) {
        // 문서 파일인 경우 OCR 모드 선택 모달 표시
        setShowOcrModal(true)
        setOcrMode(null)
        setOcrAccuracyMode('speed')
      }
    }
  }

  const handleUploadDirect = async (file: File, selectedOcrMode: string = 'portray') => {
    setUploading(true)
    setStatus('업로드 중...')
    try {
      await uploadContent(file, undefined, undefined, selectedOcrMode)
      setStatus('')
      setSelectedFile(null)

      // 파일 입력 필드 초기화
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      // 토스트 알림
      toast.success('파일이 처리 대기열에 추가되었습니다', {
        description: file.name,
      })

      // 목록 페이지로 이동하고 자동 새로고침
      router.push(`/contents?refresh=${Date.now()}`)
    } catch (error) {
      setStatus('')
      toast.error('업로드 실패', {
        description: '다시 시도해 주세요.',
      })
    } finally {
      setUploading(false)
    }
  }

  const handleModalClose = () => {
    setShowModal(false)
    setSelectedFile(null)
    setAccuracyMode('speed')
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleOcrModalClose = () => {
    setShowOcrModal(false)
    setSelectedFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleOcrUpload = async () => {
    if (!selectedFile || !ocrMode) return

    const filename = selectedFile.name
    setUploading(true)
    setStatus('업로드 중...')
    try {
      await uploadContent(selectedFile, undefined, undefined, ocrMode, ocrAccuracyMode)
      setStatus('')
      setShowOcrModal(false)
      setSelectedFile(null)
      setOcrMode(null)
      setOcrAccuracyMode('speed')

      // 파일 입력 필드 초기화
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      // 토스트 알림
      toast.success('파일이 처리 대기열에 추가되었습니다', {
        description: filename,
      })

      // 목록 페이지로 이동하고 자동 새로고침
      router.push(`/contents?refresh=${Date.now()}`)
    } catch (error) {
      setStatus('')
      toast.error('업로드 실패', {
        description: '다시 시도해 주세요.',
      })
    } finally {
      setUploading(false)
    }
  }

  const handleUpload = async () => {
    if (!selectedFile) return

    let minSpeakers: number | undefined = undefined
    let maxSpeakers: number | undefined = undefined

    if (speakerRange) {
      switch (speakerRange) {
        case 'auto':
          // 자동 파악: 둘 다 undefined
          minSpeakers = undefined
          maxSpeakers = undefined
          break
        case '1-2':
          minSpeakers = 1
          maxSpeakers = 2
          break
        case '3-6':
          minSpeakers = 3
          maxSpeakers = 6
          break
        case '7-10':
          minSpeakers = 7
          maxSpeakers = 10
          break
        case '11+':
          minSpeakers = 11
          maxSpeakers = undefined
          break
      }
    }

    const filename = selectedFile.name
    setUploading(true)
    setStatus('업로드 중...')
    try {
      await uploadContent(selectedFile, minSpeakers, maxSpeakers, undefined, undefined, accuracyMode)
      setStatus('')
      setShowModal(false)
      setSelectedFile(null)
      setSpeakerRange(null)
      setAccuracyMode('speed')

      // 파일 입력 필드 초기화
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      // 토스트 알림
      toast.success('파일이 처리 대기열에 추가되었습니다', {
        description: filename,
      })

      // 목록 페이지로 이동하고 자동 새로고침 (쿼리 파라미터로 강제 새로고침)
      router.push(`/contents?refresh=${Date.now()}`)
    } catch (error) {
      setStatus('')
      toast.error('업로드 실패', {
        description: '다시 시도해 주세요.',
      })
    } finally {
      setUploading(false)
    }
  }

  const handleButtonClick = () => {
    fileInputRef.current?.click()
  }

  return (
    <>
      <Card>
        <CardContent className="pt-6">
          <div className="space-y-2">
            <Input
              ref={fileInputRef}
              type="file"
              accept="audio/*,video/*,.txt,.png,.jpg,.jpeg,.gif,.bmp,.tiff,.webp,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx"
              onChange={handleFileSelect}
              disabled={isUploading}
              className="hidden"
            />
            <Button
              type="button"
              onClick={handleButtonClick}
              disabled={isUploading}
              className="w-full"
            >
              파일 업로드
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setShowStreamingModal(true)}
              disabled={isUploading}
              className="w-full mt-2"
              data-streaming-asr-trigger
            >
              실시간 전사 (Beta)
            </Button>
            {status && (
              <p className={status.includes('실패') ? 'text-sm text-destructive' : 'text-sm text-primary'}>
                {status}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* 오디오 파일 화자 수 선택 모달 */}
      <Dialog open={showModal} onOpenChange={setShowModal}>
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
                {[
                  { value: 'auto', label: '자동 파악' },
                  { value: '1-2', label: '1~2명' },
                  { value: '3-6', label: '3~6명' },
                  { value: '7-10', label: '7~10명' },
                  { value: '11+', label: '11명 이상' },
                ].map((option) => (
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

            <div className="mt-6">
              <Label className="text-sm font-medium mb-3 block">전사 모드</Label>
              <RadioGroup
                value={accuracyMode}
                onValueChange={(value) => setAccuracyMode(value as AccuracyMode)}
              >
                <div className="grid grid-cols-2 gap-3">
                  <Label
                    htmlFor="speed"
                    className="flex flex-col space-y-1 rounded-md border border-input bg-background p-3 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                  >
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="speed" id="speed" />
                      <span className="text-sm font-semibold">속도 우선</span>
                    </div>
                    <p className="text-xs text-muted-foreground ml-6">
                      빠른 처리
                    </p>
                  </Label>
                  <Label
                    htmlFor="accuracy"
                    className="flex flex-col space-y-1 rounded-md border border-input bg-background p-3 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                  >
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="accuracy" id="accuracy" />
                      <span className="text-sm font-semibold">정확도 우선</span>
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
              onClick={handleModalClose}
              disabled={isUploading}
            >
              취소
            </Button>
            <Button
              type="button"
              onClick={handleUpload}
              disabled={isUploading}
            >
              {isUploading ? '업로드 중...' : '업로드'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 문서 파일 OCR 모드 선택 모달 */}
      <Dialog open={showOcrModal} onOpenChange={setShowOcrModal}>
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
              onValueChange={(value) => setOcrMode(value as OcrMode)}
            >
              <div className="space-y-3">
                <Label
                  htmlFor="portray"
                  className={`flex flex-col space-y-1 rounded-md border border-input bg-background p-4 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary ${selectedFile && isOfficeDocument(selectedFile.name) ? 'opacity-50 cursor-not-allowed' : ''
                    }`}
                >
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem
                      value="portray"
                      id="portray"
                      disabled={selectedFile ? isOfficeDocument(selectedFile.name) : false}
                    />
                    <span className="text-sm font-semibold">이미지 묘사</span>
                  </div>
                  <p className="text-xs text-muted-foreground ml-6">
                    전문적인 시각으로 이미지의 대상, 인물, 상황을 분석하고 상세하게 묘사합니다.
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
                    Qwen3-VL 모델을 사용하여 문서의 텍스트와 구조를 심층적으로 분석합니다.
                    (일반 문서, 표가 포함된 문서에 권장)
                  </p>
                </Label>
              </div>
            </RadioGroup>

            <div className="mt-6">
              <Label className="text-sm font-medium mb-3 block">처리 모드</Label>
              <RadioGroup
                value={ocrAccuracyMode}
                onValueChange={(value) => setOcrAccuracyMode(value as OcrAccuracyMode)}
              >
                <div className="grid grid-cols-2 gap-3">
                  <Label
                    htmlFor="ocr-speed"
                    className="flex flex-col space-y-1 rounded-md border border-input bg-background p-3 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                  >
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="speed" id="ocr-speed" />
                      <span className="text-sm font-semibold">신속 모드</span>
                    </div>
                    <p className="text-xs text-muted-foreground ml-6">
                      빠른 처리
                    </p>
                  </Label>
                  <Label
                    htmlFor="ocr-accuracy"
                    className="flex flex-col space-y-1 rounded-md border border-input bg-background p-3 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary"
                  >
                    <div className="flex items-center space-x-2">
                      <RadioGroupItem value="accuracy" id="ocr-accuracy" />
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
              onClick={handleOcrModalClose}
              disabled={isUploading}
            >
              취소
            </Button>
            <Button
              type="button"
              onClick={handleOcrUpload}
              disabled={isUploading || !ocrMode}
            >
              {isUploading ? '업로드 중...' : '업로드'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <StreamingASRModal open={showStreamingModal} onOpenChange={setShowStreamingModal} />
    </>
  )
}
