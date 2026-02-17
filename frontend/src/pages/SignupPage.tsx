import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/shared/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/shared/components/ui/card'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { AuthBrandMark } from '@/shared/components/auth/AuthBrandMark'
import { useAuth } from '@/shared/contexts'
import type { ApiError } from '@/shared/services/api/httpClient'
import { authApi } from '@/shared/services/endpoints/auth'
import { toast } from 'sonner'

const LOGIN_ID_PATTERN = /^[a-z0-9](?:[a-z0-9._-]{2,18}[a-z0-9])?$/
const PASSWORD_PATTERN = /^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,128}$/

function extractApiDetail(error: ApiError): string | null {
  if (!error?.data || typeof error.data !== 'object') {
    return null
  }

  const detail = (error.data as { detail?: unknown }).detail
  if (typeof detail === 'string') {
    return detail
  }

  return null
}

export function SignupPage() {
  const navigate = useNavigate()
  const { signup } = useAuth()

  const [name, setName] = useState('')
  const [loginId, setLoginId] = useState('')
  const [password, setPassword] = useState('')
  const [passwordConfirm, setPasswordConfirm] = useState('')
  const [signupCode, setSignupCode] = useState('')
  const [loginIdError, setLoginIdError] = useState('')
  const [passwordError, setPasswordError] = useState('')
  const [passwordConfirmError, setPasswordConfirmError] = useState('')
  const [checkedLoginId, setCheckedLoginId] = useState('')
  const [isCheckingLoginId, setIsCheckingLoginId] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const normalizeLoginId = (value: string) => value.trim().toLowerCase()

  const validateLoginIdFormat = (value: string): boolean => {
    const normalizedLoginId = normalizeLoginId(value)
    if (!LOGIN_ID_PATTERN.test(normalizedLoginId)) {
      setLoginIdError('아이디는 4~20자의 영문 소문자, 숫자, ., _, - 만 사용할 수 있습니다.')
      return false
    }
    setLoginIdError('')
    return true
  }

  const validatePasswordFormat = (value: string): boolean => {
    if (!PASSWORD_PATTERN.test(value)) {
      setPasswordError('비밀번호는 8자 이상, 영문/숫자/특수문자를 모두 포함해야 합니다.')
      return false
    }
    setPasswordError('')
    return true
  }

  const validatePasswordConfirmation = (passwordValue: string, confirmValue: string): boolean => {
    if (passwordValue !== confirmValue) {
      setPasswordConfirmError('비밀번호와 비밀번호 재확인이 일치하지 않습니다.')
      return false
    }
    setPasswordConfirmError('')
    return true
  }

  const checkDuplicateLoginId = async (showSuccessToast = true): Promise<boolean> => {
    const normalizedLoginId = normalizeLoginId(loginId)
    if (!validateLoginIdFormat(normalizedLoginId)) {
      return false
    }

    setIsCheckingLoginId(true)
    try {
      const result = await authApi.checkLoginId(normalizedLoginId)
      if (!result.available) {
        setLoginIdError('이미 사용 중인 아이디입니다.')
        setCheckedLoginId('')
        return false
      }

      setLoginIdError('')
      setCheckedLoginId(normalizedLoginId)
      if (showSuccessToast) {
        toast.success('사용 가능한 아이디입니다.')
      }
      return true
    } catch (error) {
      const apiError = error as ApiError
      if (apiError?.status === 400) {
        const detailMessage = extractApiDetail(apiError)
        setLoginIdError(detailMessage || '아이디 형식을 확인해주세요.')
        return false
      }

      if (apiError?.status === 409) {
        setLoginIdError('이미 사용 중인 아이디입니다.')
        return false
      }

      setLoginIdError('아이디 중복 확인에 실패했습니다. 서버 연결을 확인해주세요.')
      return false
    } finally {
      setIsCheckingLoginId(false)
    }
  }

  const onSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isSubmitting) return

    const normalizedLoginId = normalizeLoginId(loginId)
    const isLoginIdValid = validateLoginIdFormat(normalizedLoginId)
    const isPasswordValid = validatePasswordFormat(password)
    const isPasswordConfirmValid = validatePasswordConfirmation(password, passwordConfirm)
    const normalizedSignupCode = signupCode.trim()

    if (!normalizedSignupCode) {
      toast.error('가입 인증코드를 입력해주세요.')
      return
    }

    if (!isLoginIdValid || !isPasswordValid || !isPasswordConfirmValid) {
      toast.error('입력값 검증에 실패했습니다. 안내 메시지를 확인해주세요.')
      return
    }

    if (checkedLoginId !== normalizedLoginId) {
      const isDuplicateChecked = await checkDuplicateLoginId(false)
      if (!isDuplicateChecked) {
        toast.error('아이디 중복 확인이 필요합니다.')
        return
      }
    }

    setIsSubmitting(true)
    try {
      await signup({
        name,
        login_id: normalizedLoginId,
        password,
        signup_code: normalizedSignupCode,
      })
      toast.success('회원가입이 완료되었습니다.')
      navigate('/', { replace: true })
    } catch (error) {
      const apiError = error as ApiError
      if (apiError?.status === 403) {
        toast.error('가입 인증코드가 유효하지 않습니다.')
      } else {
        toast.error('회원가입에 실패했습니다. 입력 정보를 확인해주세요.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  const onLoginIdChange = (value: string) => {
    setLoginId(value)
    if (checkedLoginId && checkedLoginId !== normalizeLoginId(value)) {
      setCheckedLoginId('')
    }
    if (loginIdError) {
      setLoginIdError('')
    }
  }

  const onPasswordChange = (value: string) => {
    setPassword(value)
    if (passwordError) {
      setPasswordError('')
    }
    if (passwordConfirm) {
      validatePasswordConfirmation(value, passwordConfirm)
    }
  }

  const onPasswordConfirmChange = (value: string) => {
    setPasswordConfirm(value)
    if (passwordConfirmError) {
      setPasswordConfirmError('')
    }
  }

  const isLoginIdChecked = checkedLoginId === normalizeLoginId(loginId) && checkedLoginId.length > 0

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
        <Card className="w-full max-w-md border-zinc-800/70 bg-zinc-900/80 text-zinc-50 backdrop-blur">
          <CardHeader className="space-y-3 text-center">
            <AuthBrandMark tone="emerald" />
            <CardTitle className="text-2xl">회원가입</CardTitle>
            <CardDescription className="text-zinc-300">
              새 계정을 만들고 바로 서비스를 시작하세요.
            </CardDescription>
          </CardHeader>

          <CardContent>
            <form className="space-y-4" onSubmit={onSubmit}>
              <div className="space-y-2">
                <Label htmlFor="name">이름</Label>
                <Input
                  id="name"
                  type="text"
                  autoComplete="name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  className="border-zinc-700 bg-zinc-900/70"
                />
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="loginId">아이디</Label>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="bg-emerald-100/90 text-emerald-950 shadow-sm hover:bg-emerald-100"
                    onClick={() => void checkDuplicateLoginId()}
                    disabled={isCheckingLoginId || isSubmitting}
                  >
                    {isCheckingLoginId ? '확인 중...' : '중복 확인'}
                  </Button>
                </div>
                <Input
                  id="loginId"
                  type="text"
                  autoComplete="username"
                  value={loginId}
                  onChange={(event) => onLoginIdChange(event.target.value)}
                  onBlur={() => {
                    if (loginId) {
                      validateLoginIdFormat(loginId)
                    }
                  }}
                  required
                  className="border-zinc-700 bg-zinc-900/70"
                />
                {loginIdError ? (
                  <p className="text-xs text-red-300">{loginIdError}</p>
                ) : isLoginIdChecked ? (
                  <p className="text-xs text-emerald-300">중복 확인 완료된 아이디입니다.</p>
                ) : null}
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">비밀번호</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  value={password}
                  onChange={(event) => onPasswordChange(event.target.value)}
                  onBlur={() => {
                    if (password) {
                      validatePasswordFormat(password)
                    }
                  }}
                  required
                  className="border-zinc-700 bg-zinc-900/70"
                />
                {passwordError ? (
                  <p className="text-xs text-red-300">{passwordError}</p>
                ) : (
                  <p className="text-xs text-zinc-400">8자 이상, 영문/숫자/특수문자 포함</p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="passwordConfirm">비밀번호 재확인</Label>
                <Input
                  id="passwordConfirm"
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  value={passwordConfirm}
                  onChange={(event) => onPasswordConfirmChange(event.target.value)}
                  onBlur={() => {
                    if (passwordConfirm) {
                      validatePasswordConfirmation(password, passwordConfirm)
                    }
                  }}
                  required
                  className="border-zinc-700 bg-zinc-900/70"
                />
                {passwordConfirmError && (
                  <p className="text-xs text-red-300">{passwordConfirmError}</p>
                )}
              </div>

              <div className="space-y-2">
                <Label htmlFor="signupCode">가입 인증코드</Label>
                <Input
                  id="signupCode"
                  type="password"
                  autoComplete="off"
                  value={signupCode}
                  onChange={(event) => setSignupCode(event.target.value)}
                  required
                  className="border-zinc-700 bg-zinc-900/70"
                />
              </div>

              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  className="w-full bg-teal-100/90 text-teal-950 hover:bg-teal-100"
                  onClick={() => navigate('/login')}
                  disabled={isSubmitting}
                >
                  취소
                </Button>
                <Button
                  type="submit"
                  className="w-full border border-white/20 bg-gradient-to-r from-emerald-300 via-teal-300 to-cyan-300 text-slate-950 shadow-[0_12px_28px_-14px_rgba(45,212,191,0.95)] hover:from-emerald-200 hover:via-teal-200 hover:to-cyan-200"
                  disabled={isSubmitting || isCheckingLoginId}
                >
                  {isSubmitting ? '처리 중...' : '확인'}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
