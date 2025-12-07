# 개발 모드 설정 가이드

## 문제 상황

개발 모드에서 프론트엔드를 실행할 때, 서버 사이드 렌더링(SSR) 중 백엔드 API 호출이 실패하는 문제가 발생할 수 있습니다.

**에러 예시:**
```
Error: getaddrinfo ENOTFOUND asr-backend
```

이는 서버 사이드에서 Docker 네트워크의 `asr-backend` 호스트를 찾을 수 없기 때문입니다.

## 해결 방법

### 1. 환경 변수 설정

프로젝트 루트에 `.env.local` 파일을 생성하고 다음 내용을 추가하세요:

```bash
# 서버 사이드에서 사용할 API URL (Next.js 서버 컴포넌트에서 사용)
# 개발 모드: localhost 사용
API_BASE_URL=http://localhost:8000/api

# 클라이언트 사이드에서 사용할 API URL (브라우저에서 접근 가능)
# 개발 모드: localhost 사용
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api
```

### 2. 프로덕션 모드 (Docker)

프로덕션 모드에서는 Docker Compose가 자동으로 환경 변수를 설정합니다:
- `API_BASE_URL=http://asr-backend:8000/api` (Docker 내부 네트워크)
- `NEXT_PUBLIC_API_BASE_URL=/api` (nginx 프록시 사용)

### 3. CORS 경고 해결 (선택사항)

외부 도메인에서 개발 서버에 접근하는 경우, `next.config.js`에 `allowedDevOrigins`가 설정되어 있습니다.
필요시 `.env.local`에 다음을 추가하세요:

```bash
ALLOWED_DEV_ORIGINS=asr.timblo.io,your-domain.com
```

## 동작 방식

### 개발 모드
- **서버 사이드**: `API_BASE_URL` 환경 변수 사용 (기본값: `http://localhost:8000/api`)
- **클라이언트 사이드**: `NEXT_PUBLIC_API_BASE_URL` 환경 변수 사용 (기본값: `/api`)

### 프로덕션 모드 (Docker)
- **서버 사이드**: `http://asr-backend:8000/api` (Docker 내부 네트워크)
- **클라이언트 사이드**: `/api` (nginx 프록시)

## 확인 방법

1. 백엔드가 `http://localhost:8000`에서 실행 중인지 확인
2. `.env.local` 파일이 올바르게 설정되었는지 확인
3. 개발 서버 재시작: `npm run dev`

## 참고

- `.env.local` 파일은 Git에 커밋되지 않습니다 (`.gitignore`에 포함)
- 환경 변수는 서버 재시작 후에만 적용됩니다

