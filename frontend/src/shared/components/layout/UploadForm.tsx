import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadApi } from '@/shared/services/endpoints/upload'
import { dispatchContentsRefresh } from '@/shared/hooks/useContents'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/shared/components/ui/dialog'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/shared/components/ui/radio-group'
import { Card, CardContent } from '@/shared/components/ui/card'
import { toast } from 'sonner'

type SpeakerRange = 'auto' | '1-2' | '3-6' | '7-10' | '11+' | null
type OcrMode = 'portray' | 'document' | null

export default function UploadForm() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [status, setStatus] = useState<string>('')
  const [isUploading, setUploading] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const [showOcrModal, setShowOcrModal] = useState(false)
  const [showOfficeModal, setShowOfficeModal] = useState(false)
  const [speakerRange, setSpeakerRange] = useState<SpeakerRange>('auto')
  const [ocrMode, setOcrMode] = useState<OcrMode>(null)
  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const isAudioFile = (filename: string): boolean => {
    const audioExtensions = [
      '.mp3',
      '.wav',
      '.m4a',
      '.aac',
      '.ogg',
      '.flac',
      '.wma',
      '.mp4',
      '.avi',
      '.mkv',
      '.mov',
      '.webm',
    ]
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
    return audioExtensions.includes(ext)
  }

  const isDocumentFile = (filename: string): boolean => {
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
    // 허용된 문서 파일: PDF, 이미지, 텍스트만 (Office 문서 제외)
    const allowedDocumentExtensions = [
      '.txt',
      '.pdf',
      '.png',
      '.jpg',
      '.jpeg',
      '.gif',
      '.bmp',
      '.tiff',
      '.webp',
    ]
    return allowedDocumentExtensions.includes(ext)
  }

  const isOfficeDocument = (filename: string): boolean => {
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'))
    // Office 문서 확장자 (현재 지원 안 함)
    const officeExtensions = ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']
    return officeExtensions.includes(ext)
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null
    if (file) {
      // 허용되지 않은 파일 체크
      if (
        !isAudioFile(file.name) &&
        !isDocumentFile(file.name) &&
        !isOfficeDocument(file.name)
      ) {
        setStatus(
          '지원하지 않는 파일 형식입니다. 오디오/비디오 파일, PDF, 이미지 파일, 텍스트 파일 또는 Office 문서만 업로드 가능합니다.'
        )
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
      } else if (isOfficeDocument(file.name)) {
        // Office 문서는 별도 확인 모달 표시
        setShowOfficeModal(true)
      }
    }
  }

  const handleModalClose = () => {
    setShowModal(false)
    setSelectedFile(null)
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
      await uploadApi.uploadContent(selectedFile, {
        ocrMode,
        ocrAccuracyMode: 'speed', // OCR 컨테이너 단일화 (dots.ocr)
      })
      setStatus('')
      setShowOcrModal(false)
      setSelectedFile(null)
      setOcrMode(null)

      // 파일 입력 필드 초기화
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      toast.success('파일이 처리 대기열에 추가되었습니다', {
        description: filename,
      })

      navigate('/contents')
      dispatchContentsRefresh()
    } catch (error) {
      setStatus('')
      toast.error('업로드 실패', {
        description: '다시 시도해 주세요.',
      })
    } finally {
      setUploading(false)
    }
  }

  const handleOfficeModalClose = () => {
    setShowOfficeModal(false)
    setSelectedFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleOfficeUpload = async () => {
    if (!selectedFile) return

    const filename = selectedFile.name
    setUploading(true)
    setStatus('업로드 중...')
    try {
      await uploadApi.uploadContent(selectedFile, {
        ocrMode: 'document',
        ocrAccuracyMode: 'speed',
      })
      setStatus('')
      setShowOfficeModal(false)
      setSelectedFile(null)

      // 파일 입력 필드 초기화
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      toast.success('파일이 처리 대기열에 추가되었습니다', {
        description: filename,
      })

      navigate('/contents')
      dispatchContentsRefresh()
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
      await uploadApi.uploadContent(selectedFile, {
        minSpeakers,
        maxSpeakers,
        accuracyMode: 'speed', // ASR 컨테이너 단일화 (whisper-turbo)
      })
      setStatus('')
      setShowModal(false)
      setSelectedFile(null)
      setSpeakerRange(null)

      // 파일 입력 필드 초기화
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      toast.success('파일이 처리 대기열에 추가되었습니다', {
        description: filename,
      })

      navigate('/contents')
      dispatchContentsRefresh()
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
            {status && (
              <p
                className={
                  status.includes('실패')
                    ? 'text-sm text-destructive'
                    : 'text-sm text-primary'
                }
              >
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
            <Button type="button" onClick={handleUpload} disabled={isUploading}>
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
                  className={`flex flex-col space-y-1 rounded-md border border-input bg-background p-4 hover:bg-accent hover:text-accent-foreground cursor-pointer [&:has([data-state=checked])]:border-primary ${
                    selectedFile && isOfficeDocument(selectedFile.name)
                      ? 'opacity-50 cursor-not-allowed'
                      : ''
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
                    Qwen3-VL 모델을 사용하여 문서의 텍스트와 구조를 심층적으로
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

      {/* Office 문서 업로드 확인 모달 */}
      <Dialog open={showOfficeModal} onOpenChange={setShowOfficeModal}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>문서 업로드</DialogTitle>
            <DialogDescription>
              선택한 문서에서 텍스트를 추출하여 요약합니다.
            </DialogDescription>
          </DialogHeader>

          <div className="py-4">
            <div className="flex items-center space-x-3 rounded-md border p-4">
              <div className="text-2xl">📄</div>
              <div className="flex-1 space-y-1">
                <p className="text-sm font-medium leading-none">
                  {selectedFile?.name}
                </p>
                <p className="text-xs text-muted-foreground">
                  {(selectedFile?.size ? (selectedFile.size / 1024 / 1024).toFixed(2) : 0)} MB
                </p>
              </div>
            </div>
            <p className="text-sm text-muted-foreground mt-4">
              * MarkItDown을 사용하여 텍스트를 추출합니다.<br />
              * 이미지나 복잡한 레이아웃은 제외될 수 있습니다.
            </p>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={handleOfficeModalClose}
              disabled={isUploading}
            >
              취소
            </Button>
            <Button
              type="button"
              onClick={handleOfficeUpload}
              disabled={isUploading}
            >
              {isUploading ? '업로드 중...' : '업로드'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
