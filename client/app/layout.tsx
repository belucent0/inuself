'use client'

import { useEffect, useState } from 'react'
import './globals.css'
import Sidebar from '@/components/Sidebar'

// 클라이언트 환경변수에서 관리자 계정 정보 읽기
const ADMIN_USERNAME = process.env.NEXT_PUBLIC_ADMIN_USERNAME || 'admin'
const ADMIN_PASSWORD = process.env.NEXT_PUBLIC_ADMIN_PASSWORD || 'admin123'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isChecking, setIsChecking] = useState(true)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    // localStorage에 인증 플래그가 있으면 통과
    const authFlag = localStorage.getItem('admin_auth')
    if (authFlag === 'true') {
      setIsAuthenticated(true)
      setIsChecking(false)
    } else {
      setIsChecking(false)
    }
  }, [])

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    // 클라이언트에서 직접 검증
    if (username === ADMIN_USERNAME && password === ADMIN_PASSWORD) {
      localStorage.setItem('admin_auth', 'true')
      setIsAuthenticated(true)
    } else {
      setError('인증 실패')
    }
  }

  if (isChecking) {
    return (
      <html lang="ko">
        <body>
          <div style={{ padding: '2rem', textAlign: 'center' }}>로딩 중...</div>
        </body>
      </html>
    )
  }

  if (!isAuthenticated) {
    return (
      <html lang="ko">
        <body>
          <div style={{
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
          }}>
            <div style={{
              backgroundColor: 'white',
              padding: '2rem',
              borderRadius: '8px',
              minWidth: '300px',
            }}>
              <h2 style={{ marginBottom: '1rem' }}>관리자 로그인</h2>
              <form onSubmit={handleLogin}>
                <div style={{ marginBottom: '1rem' }}>
                  <label style={{ display: 'block', marginBottom: '0.5rem' }}>
                    계정
                  </label>
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    required
                    autoFocus
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      border: '1px solid #ccc',
                      borderRadius: '4px',
                      boxSizing: 'border-box',
                    }}
                  />
                </div>
                <div style={{ marginBottom: '1rem' }}>
                  <label style={{ display: 'block', marginBottom: '0.5rem' }}>
                    비밀번호
                  </label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    style={{
                      width: '100%',
                      padding: '0.5rem',
                      border: '1px solid #ccc',
                      borderRadius: '4px',
                      boxSizing: 'border-box',
                    }}
                  />
                </div>
                {error && (
                  <p style={{ color: 'red', marginBottom: '1rem', fontSize: '0.9rem' }}>{error}</p>
                )}
                <button
                  type="submit"
                  style={{
                    width: '100%',
                    padding: '0.75rem',
                    backgroundColor: '#2196F3',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    fontSize: '1rem',
                  }}
                >
                  로그인
                </button>
              </form>
            </div>
          </div>
        </body>
      </html>
    )
  }

  return (
    <html lang="ko">
      <body>
        <div className="layout">
          <Sidebar />
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  )
}


