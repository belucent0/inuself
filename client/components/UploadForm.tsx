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

type SpeakerRange = '1-2' | '3-6' | '7-10' | '11+' | null

export default function UploadForm() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [status, setStatus] = useState<string>('')
  const [isUploading, setUploading] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const [speakerRange, setSpeakerRange] = useState<SpeakerRange>(null)
  const router = useRouter()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null
    if (file) {
      setSelectedFile(file)
      setShowModal(true)
      setSpeakerRange(null)
    }
  }

  const handleModalClose = () => {
    setShowModal(false)
    setSelectedFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const handleUpload = async () => {
    if (!selectedFile) return
    
    let minSpeakers: number | undefined = undefined
    let maxSpeakers: number | undefined = undefined
    
    if (speakerRange) {
      switch (speakerRange) {
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
    
    setUploading(true)
    setStatus('업로드 중...')
    try {
      await uploadContent(selectedFile, minSpeakers, maxSpeakers)
      setStatus('업로드 완료! 큐에 등록되었습니다.')
      setShowModal(false)
      setSelectedFile(null)
      setSpeakerRange(null)
      
      // 파일 입력 필드 초기화
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
      
      // 목록 페이지로 이동하고 자동 새로고침 (쿼리 파라미터로 강제 새로고침)
      router.push(`/contents?refresh=${Date.now()}`)
    } catch (error) {
      setStatus('업로드 실패. 다시 시도해 주세요.')
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
              accept="audio/*,video/*"
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
              <p className={status.includes('실패') ? 'text-sm text-destructive' : 'text-sm text-primary'}>
                {status}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

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
    </>
  )
}
