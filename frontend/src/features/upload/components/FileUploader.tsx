/**
 * 파일 업로드 컴포넌트
 * - 파일 선택
 * - 파일 타입에 따른 모달 표시
 * - 업로드 처리
 */

import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Card, CardContent } from '@/shared/components/ui/card'
import { uploadApi } from '@/shared/services/endpoints/upload'
import { AudioUploadModal } from './AudioUploadModal'
import { DocumentUploadModal } from './DocumentUploadModal'
import {
  type SpeakerRange,
  type OcrMode,
  type AccuracyMode,
  isAudioFile,
  isDocumentFile,
  isOfficeDocument,
  getSpeakerRange,
} from '../types'

export function FileUploader() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [status, setStatus] = useState<string>('')
  const [isUploading, setIsUploading] = useState(false)
  const [showAudioModal, setShowAudioModal] = useState(false)
  const [showDocumentModal, setShowDocumentModal] = useState(false)

  // 오디오 옵션
  const [speakerRange, setSpeakerRange] = useState<SpeakerRange>('auto')
  const [accuracyMode, setAccuracyMode] = useState<AccuracyMode>('speed')

  // 문서 옵션
  const [ocrMode, setOcrMode] = useState<OcrMode>(null)
  const [ocrAccuracyMode, setOcrAccuracyMode] = useState<AccuracyMode>('speed')

  const navigate = useNavigate()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null
    if (!file) return

    // 허용되지 않은 파일 체크
    if (!isAudioFile(file.name) && !isDocumentFile(file.name)) {
      if (isOfficeDocument(file.name)) {
        setStatus(
          'Office 문서(.doc, .docx, .xls, .xlsx, .ppt, .pptx)는 현재 지원하지 않습니다. PDF, 이미지, 또는 텍스트 파일로 변환 후 업로드해 주세요.'
        )
      } else {
        setStatus(
          '지원하지 않는 파일 형식입니다. 오디오/비디오 파일, PDF, 이미지 파일, 또는 텍스트 파일만 업로드 가능합니다.'
        )
      }
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
      return
    }

    setSelectedFile(file)

    // 파일 타입에 따른 모달 표시
    if (isAudioFile(file.name)) {
      setShowAudioModal(true)
      setSpeakerRange('auto')
      setAccuracyMode('speed')
    } else if (isDocumentFile(file.name)) {
      setShowDocumentModal(true)
      setOcrMode(null)
      setOcrAccuracyMode('speed')
    }
  }

  const resetForm = () => {
    setSelectedFile(null)
    setStatus('')
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleAudioModalClose = () => {
    setShowAudioModal(false)
    resetForm()
  }

  const handleDocumentModalClose = () => {
    setShowDocumentModal(false)
    resetForm()
  }

  const handleAudioUpload = async () => {
    if (!selectedFile) return

    const { min, max } = getSpeakerRange(speakerRange)
    const filename = selectedFile.name

    setIsUploading(true)
    setStatus('업로드 중...')

    try {
      await uploadApi.uploadContent(selectedFile, {
        minSpeakers: min,
        maxSpeakers: max,
        accuracyMode,
      })

      setShowAudioModal(false)
      resetForm()

      toast.success('파일이 처리 대기열에 추가되었습니다', {
        description: filename,
      })

      navigate(`/contents?refresh=${Date.now()}`)
    } catch {
      toast.error('업로드 실패', {
        description: '다시 시도해 주세요.',
      })
    } finally {
      setIsUploading(false)
      setStatus('')
    }
  }

  const handleDocumentUpload = async () => {
    if (!selectedFile || !ocrMode) return

    const filename = selectedFile.name

    setIsUploading(true)
    setStatus('업로드 중...')

    try {
      await uploadApi.uploadContent(selectedFile, {
        ocrMode,
        ocrAccuracyMode,
      })

      setShowDocumentModal(false)
      resetForm()

      toast.success('파일이 처리 대기열에 추가되었습니다', {
        description: filename,
      })

      navigate(`/contents?refresh=${Date.now()}`)
    } catch {
      toast.error('업로드 실패', {
        description: '다시 시도해 주세요.',
      })
    } finally {
      setIsUploading(false)
      setStatus('')
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
              accept="audio/*,video/*,.txt,.png,.jpg,.jpeg,.gif,.bmp,.tiff,.webp,.pdf"
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

      <AudioUploadModal
        open={showAudioModal}
        onOpenChange={setShowAudioModal}
        speakerRange={speakerRange}
        onSpeakerRangeChange={setSpeakerRange}
        accuracyMode={accuracyMode}
        onAccuracyModeChange={setAccuracyMode}
        isUploading={isUploading}
        onUpload={handleAudioUpload}
        onCancel={handleAudioModalClose}
      />

      <DocumentUploadModal
        open={showDocumentModal}
        onOpenChange={setShowDocumentModal}
        filename={selectedFile?.name || ''}
        ocrMode={ocrMode}
        onOcrModeChange={setOcrMode}
        ocrAccuracyMode={ocrAccuracyMode}
        onOcrAccuracyModeChange={setOcrAccuracyMode}
        isUploading={isUploading}
        onUpload={handleDocumentUpload}
        onCancel={handleDocumentModalClose}
      />
    </>
  )
}
