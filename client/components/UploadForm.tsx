"use client"

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { uploadContent } from '@/lib/api'

export default function UploadForm() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [status, setStatus] = useState<string>('')
  const [isUploading, setUploading] = useState(false)
  const router = useRouter()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedFile) return
    setUploading(true)
    setStatus('업로드 중...')
    try {
      await uploadContent(selectedFile)
      setStatus('업로드 완료! 큐에 등록되었습니다.')
      setSelectedFile(null)
      router.refresh()
    } catch (error) {
      setStatus('업로드 실패. 다시 시도해 주세요.')
    } finally {
      setUploading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <h2>파일 업로드</h2>
      <input
        type="file"
        accept="audio/*,video/*"
        onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
        disabled={isUploading}
      />
      <button type="submit" disabled={!selectedFile || isUploading} style={{ marginTop: '0.5rem' }}>
        {isUploading ? '업로드 중...' : '업로드'}
      </button>
      {status && <p>{status}</p>}
    </form>
  )
}


