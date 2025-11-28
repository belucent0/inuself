'use client'

import Link from 'next/link'
import UploadForm from './UploadForm'

export default function Sidebar() {
  const handleLogout = () => {
    if (confirm('로그아웃하시겠습니까?')) {
      localStorage.removeItem('admin_auth')
      window.location.reload()
    }
  }

  return (
    <aside className="sidebar">
      <div>
        <h1>ASR 파이프라인</h1>
        <p>전사/화자분리 작업 현황을 확인하세요.</p>
        <nav>
          <Link href="/contents">전사된 콘텐츠</Link>
        </nav>
        <button
          onClick={handleLogout}
          style={{
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
      </div>
      <UploadForm />
    </aside>
  )
}


