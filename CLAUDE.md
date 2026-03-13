# Project Guidelines

## Design Principles

- **SOLID 원칙** 준수
  - Single Responsibility: 각 모듈/컴포넌트는 하나의 책임만
  - Open/Closed: 확장에 열려있고 수정에 닫혀있는 구조
  - Liskov Substitution: 인터페이스 기반 대체 가능성
  - Interface Segregation: 필요한 인터페이스만 의존
  - Dependency Inversion: 추상화에 의존, 구체 구현에 비의존
- **높은 유지보수성**: 명확한 관심사 분리, 일관된 패턴
- **낮은 결합도**: 컴포넌트 간 최소 의존, props/hooks를 통한 느슨한 연결

## 작업 순서 (필수)

기능 변경 시 반드시 아래 순서를 지킨다:

1. **코드 수정**
2. **테스트** — 빌드/실행/동작 확인
3. **사용자 확인** — 테스트 결과를 보고하고 승인 받기
4. **커밋 & 푸시** — 사용자가 명시적으로 요청할 때만 실행

> 커밋·푸시를 먼저 하지 않는다. 사용자 확인 없이 원격에 반영하지 않는다.
> "커밋하세요", "push해주세요" 등 명시적 요청이 없으면 절대 커밋·푸시하지 않는다.

## Git 규칙

- 사용자 확인 없이 커밋·푸시 금지
- `develop` / `main` 브랜치 직접 커밋 금지 (핫픽스 예외)
- 피처 브랜치 → develop → main 순서 준수
- 커밋 요청 시 → `/commit` 커맨드 참조 후 진행
- PR 요청 시 → `/pr` 커맨드 참조 후 진행

## Playwright MCP 테스트 (필수)

코드 작업 완료 후, **UI/기능 변경이 있다면** Playwright MCP를 호출하여 직접 브라우저 테스트를 수행한다.

### 적용 대상
- 프론트엔드 컴포넌트 수정 / 신규 UI 추가
- API 연동 흐름 변경 (백엔드 수정 포함)
- 검색·채팅·스트리밍 등 핵심 기능 변경

### 테스트 절차
1. `mcp__plugin_playwright_playwright__browser_navigate` → `http://localhost:3000` 접속
2. 변경된 기능을 실제로 조작 (클릭, 입력, 전송 등)
3. 응답/렌더링 결과 스냅샷 확인
4. 필요 시 `docker logs asr-backend` 로 백엔드 로그 병행 확인
5. 이상 없으면 사용자에게 결과 보고

### 스크린샷 주의사항
- `browser_take_screenshot` 호출 시 **`filename` 파라미터를 생략**한다
- `filename` 생략 시: 이미지가 응답에 base64로 포함되어 Claude가 직접 확인 가능 (파일 저장 없음)
- `filename` 지정 시: 프로젝트 루트에 파일이 생성되어 git untracked 파일로 남게 됨 → 사용 금지

### 적용 불필요 케이스
- 순수 리팩토링 (로직 변경 없음)
- 타입/주석 수정
- 문서 작업

### 앱 접속 정보
- **Frontend**: `http://localhost:3000`
- **Backend API**: `http://localhost:8000`
- **로그인**: ID `nature` / PW `wkdus0!!`

### Playwright MCP 실행 실패 시 (Chrome 프로파일 잠금)

**증상**: `browser_navigate` 호출 시 `"Failed to launch the browser process"` 오류, 로그에 `"이미 사용 중인 프로파일 디렉토리"` 문구

**원인**: 이전 에이전트/대화가 비정상 종료되어 Chrome 프로세스가 `mcp-chrome-32ff155` 프로파일 디렉토리를 점유 중

**해결**:
```powershell
# 1. mcp-chrome 프로파일을 사용 중인 Chrome 프로세스 확인
powershell -Command "Get-WmiObject Win32_Process -Filter \"name='chrome.exe'\" | Where-Object { \$_.CommandLine -like '*mcp-chrome*' } | Select-Object ProcessId"

# 2. 메인 프로세스(--remote-debugging-port 포함) PID를 종료
powershell -Command "Stop-Process -Id <PID> -Force"

# 3. 잠시 후 browser_navigate 재시도
```

> 자식 프로세스는 부모 종료 시 자동 정리됨. 메인 PID 하나만 종료해도 충분.

## Tech Stack
- **Frontend**: Vite + React + TypeScript + Tailwind CSS
- **UI Library**: shadcn/ui (Radix UI primitives)
- **Backend**: FastAPI (Python)
- **State**: React hooks + Context API

## docs/ 폴더 이하에 아키텍처 문서있으니 참조
- 작업 후, 아키텍처가 많이 달라졌다면 새로운 버전의 아키텍처 문서를 생성하고, 기존 아키텍처 문서는 docs/archived로 이동

## docs/issues
- 자주 발생한 문제 이슈에 대한 참조
- 사용자와의 대화 3턴 이내에 해결이 안 되면, 문제 상황을 md 파일로 issues에 기록해둠

## 서비스 로그인 계정
- URL: https://dev.inuself.me
- ID: nature
- PW: wkdus0!!