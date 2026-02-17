# 검사 이력 500 에러 (WPI 문항 파일 누락) 대응 기록

**날짜**: 2026-02-14
**상태**: 해결됨

## 증상

- 프론트 `검사 이력` 화면에서 기존 심리검사 이력이 사라진 것처럼 보임.
- API `GET /api/scan/history`가 `500`을 반환.
- 사용자 체감상 "이력이 모두 삭제됨"으로 인지됨.

## 근본 원인

백엔드 로그에서 아래 예외가 반복 확인됨.

```text
FileNotFoundError: [Errno 2] No such file or directory: '/app/app/data/wpi/i-test-question.json'
FileNotFoundError: [Errno 2] No such file or directory: '/app/app/data/wpi/me-test-question.json'
```

- `scan history` 조회 시 WPI summary를 만들기 위해 `WpiService.enrich_with_scores()`가 문항 JSON을 로드함.
- 런타임 컨테이너에서 `backend/app/data/wpi/*` 경로 파일이 누락되어 예외 발생.
- 결과적으로 이력 데이터(DB)는 존재했지만, 목록 API가 500으로 실패해 화면에 비어 보였음.

## 조치 사항

1. 런타임 기준 경로에 WPI 문항 파일 추가
   - `backend/data/wpi/i-test-question.json`
   - `backend/data/wpi/me-test-question.json`

2. 서비스 방어 로직 추가
   - `backend/app/services/wpi_service.py`
   - `enrich_with_scores()`에서 `FileNotFoundError` 발생 시 경고 로그 후 raw 데이터 반환하도록 처리
   - 의도: 문항 파일 누락 시에도 history API 전체가 500으로 죽지 않도록 완화

3. compose 충돌 정리
   - `torch-test` / `torch-test-langfuse` 혼용으로 컨테이너가 엇갈리던 상태 정리
   - 최종적으로 `asr-backend`, `asr-frontend` 모두 `C:\timblo\torch-test` 기준으로 재기동

## 검증 결과

### API

- `GET http://localhost:8000/api/scan/history` → `200 OK`
- 응답에서 이력 `total=9` 확인

### UI (Playwright)

- `http://localhost:3000/scan/history` 이동 후 이력 카드 다건 노출 확인
- 네트워크 확인:
  - `GET /api/scan/history?limit=10` → `200 OK`
- 콘솔 에러: 0건

## 영향 범위

- 영향 엔드포인트
  - `GET /api/scan/history`
  - `GET /api/scan/history/{result_id}`
  - `GET /api/scan/wpi/questions`
- 공통적으로 WPI 문항 파일 로딩 경로 누락 시 500 가능

## 재발 방지

1. WPI 문항 JSON을 코드 저장소의 런타임 경로(`backend/data/wpi/`)에 소스 오브 트루스로 유지
2. 배포 전 체크리스트에 파일 존재 검증 추가
   - `backend/data/wpi/i-test-question.json`
   - `backend/data/wpi/me-test-question.json`
3. compose project 이름/실행 경로 분리 운영으로 충돌 방지
4. history API는 데이터 enrichment 실패 시에도 목록 조회를 계속하도록 방어 로직 유지
