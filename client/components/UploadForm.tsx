"use client"

import { useState, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { uploadContent } from '@/lib/api'

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

  return (
    <>
      <form>
        <h2 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>파일 업로드</h2>
        <input
          ref={fileInputRef}
          type="file"
          accept="audio/*,video/*"
          onChange={handleFileSelect}
          disabled={isUploading}
          style={{
            width: '100%',
            padding: '0.5rem',
            marginBottom: '0.5rem',
            fontSize: '0.9rem',
            minHeight: '44px',
          }}
        />
        {status && <p style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}>{status}</p>}
      </form>

      {showModal && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.5)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
          onClick={handleModalClose}
        >
          <div
            style={{
              backgroundColor: '#fff',
              padding: '2rem',
              borderRadius: '8px',
              maxWidth: '500px',
              width: '90%',
              maxHeight: '90vh',
              overflow: 'auto',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 style={{ fontSize: '1.2rem', marginBottom: '1rem', fontWeight: 'bold' }}>
              참석자 수
              <span style={{ fontSize: '0.85rem', color: '#2196F3', marginLeft: '0.5rem', fontWeight: 'normal' }}>
                *실제 발화자를 기준으로 입력하면 인식률이 향상됩니다.
              </span>
            </h2>
            
            <div style={{ marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                {[
                  { value: '1-2' as const, label: '1~2명' },
                  { value: '3-6' as const, label: '3~6명' },
                  { value: '7-10' as const, label: '7~10명' },
                  { value: '11+' as const, label: '11명 이상' },
                ].map((option) => (
                  <label
                    key={option.value}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      cursor: 'pointer',
                      padding: '0.5rem 1rem',
                      border: speakerRange === option.value ? '2px solid #2196F3' : '2px solid #ddd',
                      borderRadius: '4px',
                      backgroundColor: speakerRange === option.value ? '#e3f2fd' : '#fff',
                      transition: 'all 0.2s',
                      color: '#333',
                    }}
                  >
                    <input
                      type="radio"
                      name="speakerRange"
                      value={option.value}
                      checked={speakerRange === option.value}
                      onChange={(e) => setSpeakerRange(e.target.value as SpeakerRange)}
                      style={{
                        marginRight: '0.5rem',
                        width: '18px',
                        height: '18px',
                        cursor: 'pointer',
                      }}
                    />
                    <span style={{ fontSize: '0.9rem', color: '#333' }}>{option.label}</span>
                  </label>
                ))}
              </div>
            </div>

            <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
              <button
                type="button"
                onClick={handleModalClose}
                disabled={isUploading}
                style={{
                  padding: '0.75rem 1.5rem',
                  backgroundColor: '#f5f5f5',
                  color: '#333',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: isUploading ? 'not-allowed' : 'pointer',
                  fontSize: '0.9rem',
                }}
              >
                취소
              </button>
              <button
                type="button"
                onClick={handleUpload}
                disabled={isUploading}
                style={{
                  padding: '0.75rem 1.5rem',
                  backgroundColor: isUploading ? '#ccc' : '#2196F3',
                  color: '#fff',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: isUploading ? 'not-allowed' : 'pointer',
                  fontSize: '0.9rem',
                }}
              >
                {isUploading ? '업로드 중...' : '업로드'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}


