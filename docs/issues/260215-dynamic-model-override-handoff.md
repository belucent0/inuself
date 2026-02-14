# 동적 모델 오버라이드 + CLIProxy 연동 작업 인수인계

**날짜**: 2026-02-15  
**상태**: 진행 중 (핵심 구현 완료, 운영 정리 필요)

## 목적

- 로컬 tier 모델과 외부 모델(codex 계열)을 요청 단위로 동적으로 선택할 수 있도록 구현.
- CLIProxyAPI(OAuth) + LiteLLM 경유 호출을 프로젝트 스택에 연결.
- 프론트 UI에서 선택한 모델이 백엔드/에이전트/메타데이터까지 일관 전달되도록 보장.

## 이번 작업에서 완료한 것

### 1) 인프라 연동

- `docker-compose.yml`에 `cli-proxy-api` 서비스 추가.
- `infra/cliproxy/config.yaml` 신규 추가.
- `infra/litellm/litellm_config.yaml`에 `codex-high|medium|low` alias 추가.
- LiteLLM -> CLIProxy 호출 경로를 `/v1` 포함 형태로 정리.
- `.env.example`에 CLIProxy/LiteLLM 관련 환경 변수 설명 추가.

### 2) 백엔드 동적 모델 오버라이드

- 요청 스키마(v1/v2 create/send/regenerate)에 `model` 필드 추가.
- `LITELLM_ALLOWED_MODELS` 기반 allowlist 검증 로직 추가.
- `context + model`을 `agent_metadata`로 구성해 `run_ai_agent`/`stream_ai_agent` 경로에 전달.
- `IntentParserNode`에서 `metadata.model`(또는 `metadata.llm_model`) 우선 사용.
- 스트림/재생성/백그라운드 완료 시 assistant metadata에 `context`를 일관 저장하도록 보강.

### 3) 프론트 모델 전달 체인

- `ChatArea`에 모델 선택 dropdown 추가(자동 + tier/codex 옵션).
- 전송/재생성 시그니처를 `(..., mode?, model?)` 형태로 확장.
- `ChatArea -> ContentChatPanel/ChatPage -> useContentChat -> chatStore -> chatStreamService -> endpoints`까지 `model` 전달 연결.
- 기존 `ChatArea` 타입 오류(`normalizeMode`, Record 인덱싱) 수정.

## 변경 파일

### 인프라

- `docker-compose.yml`
- `infra/cliproxy/config.yaml` (new)
- `infra/litellm/litellm_config.yaml`
- `.env.example`

### 백엔드

- `backend/app/core/config.py`
- `backend/app/controllers/ai_chat_controller.py`
- `backend/app/agents/nodes/intent_parser.py`

### 프론트

- `frontend/src/features/chat/components/ChatArea.tsx`
- `frontend/src/features/content/components/ContentChatPanel.tsx`
- `frontend/src/pages/ChatPage.tsx`
- `frontend/src/shared/hooks/useContentChat.ts`
- `frontend/src/shared/stores/chatStore.ts`
- `frontend/src/shared/services/chatStreamService.ts`
- `frontend/src/shared/services/endpoints/threads.ts`
- `frontend/src/shared/types/thread.ts`

## 검증 결과 (현재)

- 프론트: `npm run build` 성공.
- 동적 모델 검증은 현재 워크트리 코드 마운트 임시 백엔드(`localhost:18000`)에서 확인 완료:
  - 허용 모델(`codex-high`) 요청: 200
  - 잘못된 형식(`bad model`) 요청: 400
  - allowlist 미포함(`codex-low`) 요청: 400
  - 재생성 요청에서 `context.model`이 `tier-simple`로 저장되는 것 확인
- LiteLLM 로그에서 `codex-high` 라우팅 호출 확인.

## 남은 작업

- 모델 옵션 목록을 프론트 하드코딩 대신 서버/설정 기반으로 동기화할지 결정.
- 기본 운영 스택(다른 워크트리 포함)에서 동일 검증 루틴 재실행.
- 필요 시 E2E 테스트 스크립트(curl 또는 playwright) 고정.

## 멀티 에이전트 운영 가이드 (develop 중심)

권장 방식:

1. 모든 에이전트는 `develop`에서 작업 브랜치를 분기.
2. 각 에이전트는 독립 브랜치에서 작업 후 `develop` 대상으로 PR 생성.
3. 충돌 조정 담당(통합 담당) 1명을 지정해 `develop` 충돌/순서 문제를 조정.
4. PR 병합 후 다른 에이전트는 즉시 `develop` 재동기화 후 계속 작업.

## Docker 충돌 주의사항

- 현재 `docker-compose.yml`은 `container_name`이 다수 고정되어 있어, 같은 호스트에서 복수 워크트리 스택 동시 실행 시 충돌 가능.
- 충돌 회피 우선순위:
  1. 공유 스택 1개만 운영(권장)
  2. 에이전트별로 `docker compose -p <unique>` + 포트 분리
  3. 장기적으로 `container_name` 제거 또는 override 파일로 분리

## 다음 에이전트 체크리스트

- [ ] `develop` 최신화 후 작업 브랜치 재기반
- [ ] 동적 모델 요청(create/send/regenerate) 3종 smoke test
- [ ] `LITELLM_ALLOWED_MODELS` 운영값 검토
- [ ] PR에 검증 로그(HTTP status + 라우팅 로그) 첨부
