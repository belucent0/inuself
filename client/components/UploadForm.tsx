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
      
      // 목록 페이지로 이동하고 자동 새로고침 (쿼리 파라미터로 강제 새로고침)
      router.push(`/contents?refresh=${Date.now()}`)
    } catch (error) {
      setStatus('업로드 실패. 다시 시도해 주세요.')
    } finally {
      setUploading(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <h2 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>파일 업로드</h2>
      <input
        type="file"
        accept="audio/*,video/*"
        onChange={(e) => setSelectedFile(e.target.files?.[0] ?? null)}
        disabled={isUploading}
        style={{
          width: '100%',
          padding: '0.5rem',
          marginBottom: '0.5rem',
          fontSize: '0.9rem',
          minHeight: '44px',
        }}
      />
      <button
        type="submit"
        disabled={!selectedFile || isUploading}
        style={{
          marginTop: '0.5rem',
          width: '100%',
          padding: '0.75rem',
          backgroundColor: '#2196F3',
          color: '#fff',
          border: 'none',
          borderRadius: '4px',
          cursor: !selectedFile || isUploading ? 'not-allowed' : 'pointer',
          opacity: !selectedFile || isUploading ? 0.6 : 1,
          minHeight: '44px',
          fontSize: '0.9rem',
        }}
      >
        {isUploading ? '업로드 중...' : '업로드'}
      </button>
      {status && <p style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}>{status}</p>}
    </form>
  )
}


