# 260216 - Frontend 502 Error & Session Persistence Issues

## 문제 상황

- **날짜**: 2026-02-16
- **증상 1**: asr.timblo.io 접속 시 502 Bad Gateway 에러 발생
- **증상 2**: 로그인 후 페이지 새로고침 시 세션이 유지되지 않음 (401 Unauthorized)
- **영향 범위**: 전체 서비스 다운 (프론트엔드 접속 불가) + 인증 시스템 불안정

## 원인 분석

### 1차 진단 - 백엔드 확인
```bash
docker ps --filter "name=asr-backend"
```
- `asr-backend` 컨테이너가 **Restarting (1)** 상태로 계속 재시작 중

**백엔드 로그 에러**:
```python
File "/app/app/services/wpi_service.py", line 121
    <<<<<<< HEAD
    ^^
SyntaxError: invalid syntax
```

**원인**: Git merge conflict 마커가 도커 이미지 캐시에 남아있음

**해결**: 백엔드 컨테이너 재빌드
```bash
docker-compose build --no-cache backend
docker rm -f asr-backend
docker-compose up -d backend
```

### 2차 진단 - 프론트엔드 확인
백엔드 수정 후에도 502 에러 지속. Nginx 로그 확인:

```bash
docker logs asr-nginx
```

**에러 내용**:
```
2026/02/16 05:27:10 [error] 22#22: *21 asr-frontend could not be resolved (3: Host not found)
```

### 근본 원인 - Docker 네트워크 불일치

```bash
docker inspect asr-frontend --format '{{.NetworkSettings.Networks}}'
# playful-planet_default

docker inspect asr-nginx --format '{{.NetworkSettings.Networks}}'
# torch-test_default
```

- **`asr-frontend`와 `asr-nginx`가 서로 다른 Docker 네트워크에 존재**
- 이전 컨테이너가 다른 프로젝트 네트워크(`playful-planet_default`)에 남아있었음
- Docker DNS resolver가 다른 네트워크의 컨테이너를 찾지 못함

## 해결 방법

### 적용한 해결책

1. **프론트엔드 컨테이너를 올바른 네트워크로 재시작**
```bash
docker rm -f asr-frontend
docker-compose up -d frontend
```

2. **네트워크 동기화 확인**
```bash
docker inspect asr-frontend --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}'
# torch-test_default ✓

docker inspect asr-nginx --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}'
# torch-test_default ✓
```

3. **정상 작동 확인**
```bash
docker logs --tail 20 asr-nginx
# 172.18.0.1 - - [16/Feb/2026:05:32:10 +0000] "GET / HTTP/1.1" 200 468
# 172.18.0.1 - - [16/Feb/2026:05:32:10 +0000] "GET /assets/index-CQRBviWv.css HTTP/1.1" 200 18156

curl -I http://localhost
# HTTP/1.1 200 OK ✓
```

## 예방 방법

### 1. Docker Compose 사용 원칙
```bash
# 개별 컨테이너 재시작 시 docker-compose 사용 (네트워크 자동 관리)
docker-compose restart frontend
docker-compose up -d frontend

# 수동 제거 후에도 docker-compose로 재시작
docker rm -f asr-frontend
docker-compose up -d frontend  # ✓ 올바른 네트워크로 자동 배치
```

### 2. 네트워크 일치 여부 확인 스크립트
```bash
# 모든 asr-* 컨테이너의 네트워크 확인
for container in $(docker ps --filter "name=asr-" --format "{{.Names}}"); do
  echo "$container: $(docker inspect $container --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}')"
done
```

### 3. 병합 전 Conflict 확인
```bash
# merge conflict 마커 검색
git diff --check
grep -r "<<<<<<< HEAD" backend/
```

### 4. 도커 이미지 캐시 관리
```bash
# 프로덕션 배포 전 전체 재빌드
docker-compose build --no-cache

# 또는 개별 서비스 재빌드
docker-compose build --no-cache backend
```

### 5. CI/CD 파이프라인에 검증 추가
```yaml
# .github/workflows/ci.yml (예시)
- name: Check for merge conflicts
  run: |
    if git diff --check; then
      echo "No merge conflict markers found"
    else
      echo "Merge conflict markers detected!"
      exit 1
    fi

- name: Verify docker network consistency
  run: |
    docker-compose up -d
    ./scripts/check-network-consistency.sh
```

## 교훈

1. **Docker 네트워크 관리**
   - 컨테이너를 수동으로 제거/재시작할 때 네트워크가 변경될 수 있음
   - `docker-compose up -d`는 자동으로 올바른 네트워크에 컨테이너 배치
   - 같은 Docker Compose 프로젝트 내 컨테이너는 같은 네트워크에 있어야 통신 가능

2. **도커 이미지 캐시 문제**
   - 빌드 타임에 포함된 파일은 이미지 레이어에 고정됨
   - 로컬 코드 수정이 컨테이너에 즉시 반영되지 않을 수 있음
   - 이상 동작 발견 시 `--no-cache` 빌드 우선 시도

3. **Git Merge 후 검증 필수**
   - Merge/rebase 후 반드시 전체 빌드 테스트 실행
   - Conflict 마커(`<<<<<<<`, `=======`, `>>>>>>>`) 검색 자동화
   - 도커 이미지 재빌드 권장

4. **진단 프로세스**
   - 502 에러 발생 시 체크리스트:
     1. 백엔드 컨테이너 상태 확인 (`docker ps`)
     2. 백엔드 로그 확인 (`docker logs asr-backend`)
     3. Nginx 로그 확인 (`docker logs asr-nginx`)
     4. 네트워크 일치 여부 확인 (`docker inspect [container]`)

## 관련 파일
- `backend/app/services/wpi_service.py` (1차 문제 - merge conflict)
- `docker-compose.yml` (서비스 정의 및 네트워크 설정)
- `backend/Dockerfile` (백엔드 빌드 설정)
- `infra/nginx/nginx.conf` (리버스 프록시 설정)

## 관련 커밋
- bd33ede - 🔀 PR : feat: WPI AI 리포트 실행 경로를 백엔드로 일원화
- a04888b - feat(wpi): WPI AI 리포트 실행을 백엔드로 이관하고 워커 경로 제거

## 해결 시간
- **1차 탐지**: 2026-02-16 14:10 (502 에러 발견)
- **1차 원인 파악**: 14:15 (백엔드 로그 분석 - merge conflict)
- **1차 수정**: 14:22 (백엔드 재빌드 완료)
- **2차 탐지**: 14:27 (502 에러 지속)
- **2차 원인 파악**: 14:30 (nginx 로그 분석 - 네트워크 불일치)
- **2차 수정 완료**: 14:32 (프론트엔드 재시작, 서비스 정상화)
- **총 소요 시간**: 약 22분

## 3차 문제 - 세션 유지 실패 (JWT 인증)

### 증상
- 로그인 성공 직후: 정상 동작 (`/api/threads` 200 OK)
- 페이지 새로고침 후: 401 Unauthorized 에러 발생 (`/api/auth/me`, `/api/auth/refresh` 모두 실패)
- 로그인 페이지로 자동 리다이렉트

### 원인 분석

**1단계: JWT_SECRET_KEY 누락**
- 백엔드 로그: `WARNING: JWT_SECRET_KEY not set. Using temporary in-memory key`
- 백엔드 재시작 시마다 새로운 임시 JWT 키 생성
- 이전 키로 서명된 토큰이 새 키로는 검증 불가능

**2단계: nginx에서 Authorization 헤더 차단**
- nginx 설정에 `proxy_set_header Authorization` 명시 필요
- 명시하지 않으면 백엔드로 헤더가 전달되지 않음
- 프론트엔드는 `Authorization: Bearer <token>` 헤더를 보내지만 백엔드는 수신하지 못함

**3단계: nginx 볼륨 마운트 불일치**
- nginx 컨테이너가 다른 프로젝트 디렉토리(`eager-tiger`)의 설정 파일을 마운트하고 있었음
- 호스트의 `infra/nginx/nginx.conf` 수정이 컨테이너에 반영되지 않음
- `docker inspect`로 확인: `/eager-tiger/infra/nginx/nginx.conf` 마운트됨

### 해결 방법

**1. JWT_SECRET_KEY 환경 변수 추가**

`.env` 파일에 추가:
```bash
JWT_SECRET_KEY=jEYSMdc4T3JpddX-NflcdWXLyVLqtFB05L3pfLq8Z85uygDkVxRCuHylJ7i7vh5I4H4zktED0aflUhDWMBjA2Q
```

`docker-compose.yml`의 backend 서비스에 환경 변수 추가:
```yaml
backend:
  environment:
    - JWT_SECRET_KEY=${JWT_SECRET_KEY}
```

**2. nginx 설정에 Authorization 헤더 전달 추가**

`infra/nginx/nginx.conf` 수정:
```nginx
location /api/ {
    proxy_pass http://$backend_host;
    # ... 기존 설정 ...

    # Authorization 헤더 전달 (JWT 인증)
    proxy_set_header Authorization $http_authorization;
}

location ~ ^/ws(/|$) {
    proxy_pass http://$backend_host;
    # ... 기존 설정 ...

    # Authorization 헤더 전달 (JWT 인증)
    proxy_set_header Authorization $http_authorization;
}

location = /api/chat {
    proxy_pass http://$backend_host;
    # ... 기존 설정 ...

    # Authorization 헤더 전달 (JWT 인증)
    proxy_set_header Authorization $http_authorization;
}
```

**3. nginx 컨테이너 재생성**

올바른 경로의 설정 파일을 마운트하도록 컨테이너 재생성:
```bash
cd C:/timblo/torch-test
docker-compose up -d nginx
```

확인:
```bash
docker exec asr-nginx sh -c 'cat /etc/nginx/nginx.conf | grep -c Authorization'
# 6  (성공: Authorization 설정 6개 확인됨)
```

**4. 백엔드 재시작**

JWT_SECRET_KEY가 적용되도록 재시작:
```bash
docker-compose restart backend
```

### 심화 조사 결과 (2026-02-16 15:00-16:00)

**확인된 사실:**
1. ✅ 프론트엔드는 Authorization 헤더를 올바르게 전송 (`Bearer <token>`)
2. ✅ nginx는 Authorization 헤더를 백엔드로 전달 (`proxy_set_header Authorization $http_authorization`)
3. ✅ JWT 토큰은 Valkey에 정상 저장됨 (JTI 키로 user_id 저장 확인)
4. ✅ JWT 토큰 자체는 유효함 (Python jwt.decode로 검증 완료)
5. ✅ JWT_SECRET_KEY는 환경 변수로 올바르게 설정됨
6. ✅ 로그인 직후 `/api/threads` 요청은 200 OK (인증 성공)
7. ❌ 페이지 새로고침 후 `/api/auth/me` 요청은 401 Unauthorized

**문제의 정확한 흐름:**
1. 로그인 성공 → 토큰 localStorage에 저장
2. `/api/threads` 호출 → 200 OK (인증 성공)
3. 페이지 새로고침 → AuthContext의 `bootstrap()` 실행
4. `bootstrap()`이 `/api/auth/me` 호출
5. `/api/auth/me` → 401 Unauthorized ("만료되었거나 폐기된 액세스 토큰입니다.")
6. httpClient가 자동으로 `/api/auth/refresh` 호출
7. `/api/auth/refresh` → 401 Unauthorized ("리프레시 토큰 재사용이 감지되었습니다.")
8. httpClient가 `clearAuthTokens()` 호출 → 토큰 삭제
9. 로그인 페이지로 리다이렉트

**테스트 결과:**
- Valkey FLUSHDB 후 재테스트 → 동일한 문제
- 백엔드 컨테이너 재시작 → 동일한 문제
- 백엔드 이미지 재빌드 (코드 볼륨 마운트 제거) → 동일한 문제
- curl로 직접 `/api/auth/me` 호출 → 401 에러 ("만료되었거나 폐기된 액세스 토큰입니다.")
- 하지만 Valkey에는 토큰이 존재하고 user_id도 일치함

**의심되는 원인:**
1. **Token version 불일치**: JWT payload의 `ver: 0`과 Valkey에 저장된 token_version이 일치하지 않을 가능성
2. **백엔드 validate_access_token 로직의 버그**: 토큰이 Valkey에 있고 유효해도 401을 반환
3. **타이밍 이슈**: 로그인 직후와 새로고침 시 다른 조건이 있을 가능성
4. **Family key 문제**: Refresh token의 family key가 삭제되어 refresh 실패

### 4차 문제 해결 - Redis bytes 타입 처리 (2026-02-16 16:00)

**근본 원인 발견:**
- Valkey(Redis)에서 `redis.get()` 호출 시 **bytes 타입** 반환 (`b'string'`)
- 하지만 비교 대상은 **string 타입** (`'string'`)
- Python에서 `'string' != b'string'` → 항상 불일치 → 401 에러

**상세 로그:**
```
[AUTH] Valkey lookup - Expected: 019c6271-0c25-7b72-afb4-261b62b2f7b0
                       Found: b'019c6271-0c25-7b72-afb4-261b62b2f7b0'
[AUTH] Token validation failed - user_id mismatch
```

**수정 내역:**
`backend/app/core/auth.py`의 모든 `redis.get()` 호출 후 bytes 디코딩 추가:
```python
exists_user_id = await redis.get(f"{ACCESS_KEY_PREFIX}{access_jti}")
# Decode bytes to string if needed
if isinstance(exists_user_id, bytes):
    exists_user_id = exists_user_id.decode('utf-8')
```

**수정 위치:**
1. Line 294-297: `validate_access_token` - access token 검증
2. Line 109-112: `_get_user_token_version` - token version 조회
3. Line 229-232: `rotate_refresh_token` - refresh token record 조회
4. Line 240-243: `rotate_refresh_token` - family current JTI 조회

**테스트 결과:**
✅ 로그인 성공
✅ 새로고침 후에도 세션 유지
✅ 토큰 검증 성공

## 최종 상태
✅ **완전 해결**: asr.timblo.io 502 에러 및 JWT 인증 세션 유지 문제 모두 해결
- 502 에러: 백엔드 Docker 이미지 재빌드 및 네트워크 수정
- JWT 설정: JWT_SECRET_KEY 환경 변수 추가
- nginx 설정: Authorization 헤더 포워딩 추가
- **Redis bytes 타입**: Valkey 반환값 bytes → string 디코딩 추가
