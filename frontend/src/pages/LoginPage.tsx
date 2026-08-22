import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { Button } from '@/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/components/ui/card'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { AuthBrandMark } from '@/shared/components/auth/AuthBrandMark'
import { useAuth } from '@/shared/contexts'
import { isApiUnavailable, type ApiError } from '@/shared/services/api/httpClient'
import { toast } from 'sonner'

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const { login } = useAuth()

  const [loginId, setLoginId] = useState('')
  const [password, setPassword] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const onSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isSubmitting) return

    setIsSubmitting(true)
    try {
      await login({ login_id: loginId, password })
      toast.success('로그인되었습니다.')
      const redirectPath = (location.state as { from?: { pathname?: string } } | undefined)?.from?.pathname || '/'
      navigate(redirectPath, { replace: true })
    } catch (error) {
      const status = (error as ApiError)?.status
      if (status === 429) {
        toast.error('로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.')
      } else if (isApiUnavailable(error)) {
        toast.error('인증 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.')
      } else {
        toast.error('로그인에 실패했습니다. 아이디/비밀번호를 확인해주세요.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="auth-motion-shell">
      <div className="auth-motion-aurora" aria-hidden="true" />
      <div className="auth-motion-grid" aria-hidden="true" />
      <div className="auth-motion-sweep" aria-hidden="true" />
      <div className="auth-motion-focus" aria-hidden="true" />
      <div className="auth-motion-blob auth-motion-blob--one" aria-hidden="true" />
      <div className="auth-motion-blob auth-motion-blob--two" aria-hidden="true" />
      <div className="auth-motion-blob auth-motion-blob--three" aria-hidden="true" />
      <div className="auth-motion-vignette" aria-hidden="true" />

      <div className="relative z-10 flex min-h-screen items-center justify-center px-4 py-10">
        <Card className="w-full max-w-md border-slate-800/70 bg-slate-900/80 text-slate-50 backdrop-blur">
          <CardHeader className="space-y-3 text-center">
            <AuthBrandMark />
            <CardTitle className="text-2xl">로그인</CardTitle>
            <CardDescription className="text-slate-300">
              계정에 로그인하여 기능을 계속 이용하세요.
            </CardDescription>
          </CardHeader>

          <CardContent>
            <form className="space-y-4" onSubmit={onSubmit}>
              <div className="space-y-2">
                <Label htmlFor="loginId">아이디</Label>
                <Input
                  id="loginId"
                  type="text"
                  autoComplete="username"
                  value={loginId}
                  onChange={(event) => setLoginId(event.target.value)}
                  required
                  className="border-slate-700 bg-slate-900/70"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">비밀번호</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  className="border-slate-700 bg-slate-900/70"
                />
              </div>

              <Button
                type="submit"
                className="w-full bg-white text-slate-900 hover:bg-slate-100"
                disabled={isSubmitting}
              >
                {isSubmitting ? '로그인 중...' : '로그인'}
              </Button>
            </form>

            <p className="mt-6 text-center text-sm text-slate-300">
              아직 계정이 없으신가요?{' '}
              <Link to="/signup" className="font-medium text-slate-200 hover:text-white underline underline-offset-2">
                회원가입
              </Link>
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
