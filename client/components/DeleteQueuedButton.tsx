"use client"

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { deleteQueuedContents } from '@/lib/api'

export default function DeleteQueuedButton() {
  const [isDeleting, setIsDeleting] = useState(false)
  const [message, setMessage] = useState<string>('')
  const router = useRouter()

  const handleDelete = async () => {
    if (!confirm('대기 중인 모든 콘텐츠를 삭제하시겠습니까?')) {
      return
    }

    setIsDeleting(true)
    setMessage('')
    
    try {
      const result = await deleteQueuedContents()
      setMessage(result.message)
      router.refresh()
      
      // 3초 후 메시지 제거
      setTimeout(() => setMessage(''), 3000)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '삭제 실패')
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <div style={{ marginBottom: '1rem' }}>
      <button
        onClick={handleDelete}
        disabled={isDeleting}
        style={{
          padding: '0.5rem 1rem',
          backgroundColor: '#F44336',
          color: 'white',
          border: 'none',
          borderRadius: '4px',
          cursor: isDeleting ? 'not-allowed' : 'pointer',
          opacity: isDeleting ? 0.6 : 1,
          minHeight: '44px',
          fontSize: '0.9rem',
          width: '100%',
        }}
      >
        {isDeleting ? '삭제 중...' : '대기 중인 콘텐츠 모두 삭제'}
      </button>
      {message && (
        <p style={{ marginTop: '0.5rem', color: message.includes('실패') ? '#F44336' : '#4CAF50' }}>
          {message}
        </p>
      )}
    </div>
  )
}

