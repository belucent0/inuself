'use client'

import Link from 'next/link'
import UploadForm from './UploadForm'

type Props = {
  isOpen?: boolean
  onClose?: () => void
}

export default function Sidebar({ isOpen = false, onClose }: Props) {
  const handleLogout = () => {
    if (confirm('로그아웃하시겠습니까?')) {
      localStorage.removeItem('admin_auth')
      window.location.reload()
    }
  }

  const handleLinkClick = () => {
    if (onClose) {
      onClose()
    }
  }

  return (
    <aside className={`sidebar ${isOpen ? 'open' : ''}`}>
      <div className="sidebar-content">
        <div className="sidebar-top">
          <h1>ASR 파이프라인</h1>
          <p>전사/화자분리 작업 현황을 확인하세요.</p>
          <button
            onClick={handleLogout}
            style={{
              width: '100%',
              marginTop: '1rem',
              padding: '0.5rem 1rem',
              backgroundColor: '#666',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '0.9rem',
            }}
          >
            로그아웃
          </button>
          <UploadForm />
        </div>
        <nav>
          <Link href="/contents" onClick={handleLinkClick}>전사된 콘텐츠</Link>
          <Link href="/roadmap" onClick={handleLinkClick}>로드맵</Link>
        </nav>
      </div>
    </aside>
  )
}


