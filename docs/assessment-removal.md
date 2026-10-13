# 심리검사 기능 및 자원 제거 기록

## 2026-10-14: 이관 확인 후 자원 정리 완료

2026-10-13의 기능 제거 작업에 이어, 별도 검사 앱으로 인수된 원본과 불필요한 검사 전용 자원을 정리했습니다. 아래 10월 13일 기록의 “원본 보존·DB 미검증”은 당시 상태이며, 현재 파일 보관 위치는 이 절을 기준으로 합니다. 기존 개인별 검사 기록은 여전히 inuself DB에 보존합니다.

- 이관 저장소: 비공개 `belucent0/inventory-app`.
- 원본 보관 커밋: `fe30c3bcd6df355d3cc669d1b3a1b7af2fff4dfd`.
- 원본 보관 위치: `reference/inuself/`, 목록은 해당 디렉터리의 `manifest.json`.
- 이번 삭제 내역: [파일별 경로·크기·SHA-256·복구 구분](assessment-resource-removal-manifest.json).
- 원 작업 Codex 세션: `01a1354a-fa77-71a1-ab11-eff1cb5bc72c`. 최초 정리·검증 단계에서는 커밋·푸시·배포를 수행하지 않았으며, 이후 사용자의 명시적 요청에 따라 아래 develop 반영 절차를 진행합니다.

| 구분 | 삭제 수 | 확인 및 복구 범위 |
|---|---:|---|
| 이관 완료 원본 | 85 | PDF 24개, 상세 해설 25개, 실행 해설 35개, 해석 관점 메모 1개. 삭제 직전 원본·inventory 로컬 사본·위 커밋 Git blob의 SHA-256과 크기 일치를 확인. Git 보관본으로 바이트 그대로 복구 가능 |
| 문항 JSON 사본 | 6 | `backend/data/wpi/`, `backend/app/data/wpi/`, `docs/personal/`의 두 문항 파일씩. inventory `src/data/wpi/`의 로컬 및 위 커밋과 JSON 내용 일치. 정본으로 내용 복구 가능하지만 원본 사본별 개행까지 동일하다는 뜻은 아님 |
| 폐기한 검사 전용 자료 | 17 | 중복·추가 추출판·진단 산출물 3개, 변환 도구 5개, 생성 캐시 1개, 이전 해설·작성 지침 2개, 미연결 프롬프트·자리표시자 2개, 폐기된 검사 설계 4개. 이관하지 않았고 원문 복구를 보장하지 않음 |
| **합계** | **108** | 파일별 허용 목록에 한해서 삭제. 폴더 일괄 삭제 없음 |

폐기한 설계 4개는 `docs/personal/{01-wpi-psychology-system,04-wpi-combination-types,05-wpi-philosophy-and-interpretation-principles}.md`와 `docs/plans/260215-wpi-125-routing-spec.md`입니다. 이전판·추가 추출판·프롬프트·설계의 일부는 원본과 내용이 달랐으며, 중복이라고 단정해서 삭제한 것이 아니라 현재 제품에서 사용하지 않기로 한 자료입니다. Git 제외 자료는 기존 inuself Git 이력으로 복원할 수 없습니다.

### 보존 경계

- DB·테이블·볼륨·ORM·Alembic은 변경하지 않았습니다. `ScanResult`, `User.scan_results`, `Client.wpi_scan_id`를 유지합니다. 개인별 검사 기록의 건수·이관·복구 여부는 이번 작업에서 확인하지 않았습니다.
- `docs/personal/02-memory-and-user-embedding.md`, `03-full-system-evaluation.md`는 메모리·개인화·평가가 혼합된 과거 기획이므로 보존하고 종료된 검사 연계를 명시했습니다.
- `docs/plans/wpi-psychology-analysis-plan.md`는 문항 검사와 별개인 상담 녹음·전사 분석 기획이므로 보존합니다.
- 과거 아키텍처·이슈·품질 결과는 역사 기록으로 유지합니다. 공용 차트, 일반 LLM 워커, 파일 상태 점검용 `/api/admin/watchdog/scan`도 유지합니다.
- 원본을 다시 참조할 때는 inventory의 보관본을 사용합니다. inuself 런타임에 자료·프롬프트·검사 메뉴를 다시 연결하지 않습니다.

### 이번 검증

- 삭제 전에 108개 절대경로가 `C:/timblo/torch-test` 안의 일반 파일인지, 해시와 크기가 사전 목록과 일치하는지 다시 확인했습니다. 개별 `Remove-Item -LiteralPath`로만 제거하고 108개 모두 사라진 것을 확인했습니다.
- 85개 원본의 로컬 사본과 Git 커밋 내용, 문항 6개 JSON 내용을 대조했습니다. 비공개 저장소 여부와 보관 커밋이 inventory HEAD의 조상인 것도 확인했습니다.
- frontend `npm run build` (`tsc -b` + Vite) 통과. 기존 Browserslist 데이터 및 큰 번들 경고가 있으나 빌드는 성공했습니다.
- backend 인증 경계·채팅 라우팅·검사 경로 종료 테스트 **44개 통과**. `.scratch/run-assessment-checks.py`에서 초기 스토리지 점검을 대역 처리하고 테스트 프로세스의 `CORS_ORIGINS=http://localhost:5173`을 지정했습니다. 실제 FastAPI 라우트의 검사 API 9개 404 및 OpenAPI 제외도 포함합니다. 기존 Pydantic 사용 방식 경고 7개가 있습니다.
- 로컬 Playwright/Chromium으로 1440px 데스크톱·390px 모바일의 메뉴, 콘텐츠·대화 기록 이동, 종료된 화면 경로 5개의 404를 확인했습니다. 검사 API 호출·브라우저 실행 오류는 없었습니다. 인증·목록 API는 대역 처리했으므로 운영 로그인·실제 DB 조회를 검증한 것은 아닙니다. 최초 실행은 잘못된 작업 디렉터리에서 Tailwind 설정을 찾지 못해 실패했으며, `frontend` 디렉터리에서 기존 검증 스크립트를 다시 실행해 통과했습니다.
- 수정 Python 7개 AST 파싱, Compose YAML 파싱, `git diff --check` 통과. venv·캐시를 제외한 실행 소스의 WPI 검색 결과는 보존하기로 한 `Client.wpi_scan_id`뿐입니다.
- 독립 최종 리뷰에서 삭제 108개·이관본 해시·DB ORM의 의미상 동일성·실행 코드 참조·문서 보존 경계를 재확인했습니다. 수정이 필요한 기능·데이터 결함은 없었으며 아키텍처 변경표의 보관 위치 표현을 현재 상태에 맞게 보완했습니다.
- 운영 로그인·DB 기록 조회·운영 배포는 이번 범위에 포함하지 않습니다.

## 2026-10-14 develop 반영 검증

사용자의 커밋·develop push 요청 후 최신 `origin/develop`(`b46736b`)을 가져왔습니다. 로컬 작업 브랜치가 원격과 갈라져 있어 제거 변경만 별도 worktree의 피처 브랜치에 적용했습니다. 최신 Agent Worker·ROCm·인증 설정·문서를 유지하면서 심리검사 항목만 제거했고, v1.2 보관본도 최신 develop 내용을 기준으로 만들었습니다. 통합본에서 프론트엔드 빌드, 백엔드 44개 테스트, 로컬 1440px·390px 브라우저 검사, Python AST·Compose 구문 검증을 다시 통과했습니다. 기존 저장소의 무관한 변경·임시 파일과 Git 제외 문서는 커밋 대상에서 제외합니다. 운영 배포는 수행하지 않습니다.

develop은 PR 경유만 허용되어 [PR #178](https://github.com/belucent0/inuself/pull/178)을 생성했습니다. 최초 CI에서 프론트엔드·게이트웨이는 통과했으나 백엔드·Agent 검사는 `greenlet` 미설치로 SQLAlchemy asyncio import 단계에서 멈췄습니다. 검사 제거에 따른 실행 실패가 아니라 비동기 런타임 의존성 선언 누락이므로 `sqlalchemy[asyncio]`를 명시하고 Docker에서 쓰는 `uv.lock`을 갱신했습니다. 잠긴 179개 패키지의 버전은 모두 유지했습니다. CI의 pip 설치 경로와 Docker의 frozen uv 경로 모두 해당 의존성을 포함하도록 하는 최소 보완입니다.

---

## 2026-10-13: 최초 기능 제거 당시 기록

작성일: 2026-10-13. inuself의 실제 로컬 저장소는 `C:/timblo/torch-test`, 별도 검사 앱은 `C:/timblo/inventory-app`입니다. 비교 기준 원본 커밋은 `3d6fe8e5b5cdc9916c82cb52015a70b91b9926fa`입니다.

검사 기능은 inuself 코드에서 제거하고, 원본 자료와 기존 개인별 검사 기록은 보존합니다. 이 작업에는 DB 삭제·자료 이관·운영 배포가 포함되지 않습니다.

## inventory-app에 있는 자료

| 자료 | 원본 경로 | 대조 결과 |
|---|---|---|
| 자기평가·타인평가 문항 | `backend/data/wpi/{i,me}-test-question.json` | inventory-app의 `src/data/wpi/` 두 파일과 원본 바이트 SHA-256 일치. 각각 30문항 |
| 문항 사본 | `backend/app/data/wpi/`, `docs/personal/`의 같은 JSON 두 파일씩 | 줄바꿈·끝 개행 차이만 있으며 JSON 내용 동일 |
| 채점 | 제거 전 `backend/app/services/wpi_service.py` | inventory-app에 이식되어 있고 원본 Python 결과 50쌍을 `tests/fixtures/original-scores.json`으로 보유 |
| 실행용 해설 35개 | `docs/wpis/` 유형별 30개 및 `_common/` 5개 | inventory-app 가져오기 manifest에 포함되고 README에 Neon 이관 기록이 있음. 원문·SHA-256을 `interpretation_materials`에 저장하는 구조. 현재 DB의 행·해시 일치는 네트워크 제한으로 미검증 |

문항 SHA-256:

- `i-test-question.json`: `a7a9a38bbc5951ae0370633722b67ec305519182673c6b5ac10d3e13d9bf8988`
- `me-test-question.json`: `c659996cac003d17b953fec5eeb9ccd50db29fd3ebe9b73ff904ee30189f5f7d`

해설 35개는 `{realist,romanticist,humanist,idealist,agent}/{default,relation,trust,manual,self,culture}.txt` 30개와 `_common/{relation,trust,manual,self,culture}_base.txt` 5개입니다. DB 이관 여부와 원문 보관 여부를 구분하며, 현재 DB를 재확인하기 전에도 로컬 원본은 유지합니다.

## inventory-app 코드·이관 목록에 없는 원문·참고 자료

| 자료 | 정확한 위치 | 보존 이유 |
|---|---|---|
| PDF 원문 24개 | `docs/wpi/*.pdf` | inventory-app 파일과 해설 이관 manifest에 없음. 총 7,971,420바이트 |
| 상세 해설 txt 25개 | `docs/wpi/`의 기본 유형 조합 파일 | 현재 해설 35개와 공백을 정규화해도 동일 파일이 없음. 일부 추출 오류가 있어 PDF도 함께 보관 |
| 추가 추출판 | `docs/wpi/agent_culture_.txt` | `agent_culture.txt`와 내용이 다름 |
| 해설 이전판 | `docs/wpis/realist/default_backup.txt` | 현재 `default.txt`와 다르고 이관 manifest에서 제외됨 |
| 자료 작성 지침 | `docs/wpis/_common/README.md` | 이관 manifest에서 제외된 참고 자료 |
| 실제 해설 프롬프트 | `backend/app/prompts/wpi_report.py` | 기존 해설 방식 참고 자료. 런타임 import·export는 제거하고 원본 파일 유지 |
| 해석 철학·유형·조합 설명 | `docs/personal/0-wpi-consul-model.md`, `01-wpi-psychology-system.md`, `04-wpi-combination-types.md`, `05-wpi-philosophy-and-interpretation-principles.md` | inventory-app에서 동일 파일이 없음. 기존 구현 설명과 도메인 참고 내용이 혼합되어 있으며 현행 기능 문서로 사용하지 않음 |
| 해석 라우팅 설계 | `docs/plans/260215-wpi-125-routing-spec.md` | 대응 페어·gap 버킷·근거 추적·문장 검증을 정리한 참고 자료. 현행 개발 계획에서 제외 |

PDF 파일 그룹:

- `agent_{culture,manual,relation,self,trust}.pdf` — 5개
- `humanist_{culture,manual,self,trust}.pdf`와 `human_relation.pdf` — 5개
- `idealist_{culture,manual,relation,self,trust}.pdf` — 5개
- `realist_{culture,manual,self,trust}.pdf` — 4개
- `romanticist_{culture,manual,relation,self,trust}.pdf` — 5개

`realist_relation.pdf`는 없고 해당 txt만 있습니다. humanist/relation 원본의 실제 이름은 `human_relation.pdf/.txt`입니다.

`docs/wpi/`에는 PDF 24개·txt 28개·변환 Python 5개, 총 57개 파일이 있습니다. txt 28개 중 25개 기본판과 추가 추출판 외의 `realist_relation copy.txt`는 원본과 SHA-256이 같은 사본이고, `analysis_result.txt`는 264바이트 진단 산출물입니다. 두 파일은 고유 해설 원문이 아니지만 이번 작업에서는 함께 보존합니다.

`docs/personal/02-memory-and-user-embedding.md`, `03-full-system-evaluation.md`, `docs/plans/wpi-psychology-analysis-plan.md`는 기존 개인화·평가·상담 분석 구현 계획입니다. `quality_test_reports/ci/wpi_quality_candidate.json`은 fixture 2건의 품질 평가 산출물입니다. 이들은 새 검사 문항·개인 응답 데이터셋으로 분류하지 않습니다.

## 제거한 기능·코드·설정

| 영역 | 제거 대상 |
|---|---|
| 메뉴·화면 경로 | 심리검사 메뉴, 검사하기·이력 하위 메뉴, `/scan`, `/scan/wpi`, `/scan/wpi/result`, `/scan/history`, `/scan/history/:resultId` |
| 페이지 | `frontend/src/pages/{ScanPage,ScanHistoryPage,WpiTestPage,WpiResultPage}.tsx` |
| 프론트 검사 모듈 | `frontend/src/features/scan/`의 API·훅·타입·차트·export 5개 파일 |
| 공유 연결부 | 검사 페이지 export, 라우터 등록, 페이지 제목·레이아웃 분기, `shared/config/scan.ts`와 export |
| 백엔드 검사 모듈 | `scan_controller.py`, `scan_repository.py`, `schemas/wpi.py`, `services/wpi_service.py`, `services/wpi_profile_parser.py` |
| AI 해설 연결 | `prompts/wpi_report_example.py` 스텁, WPI 프롬프트 import·export, 검사 전용 이벤트 추적·워커 결과 발행 도우미 |
| 실행 설정 | `docker-compose.yml`의 WPI 전용 모델·시간 제한 및 `docs/wpis` 마운트, `.env.example`의 WPI 설정 |
| 현재 기능 문서 | README 기능표, 현재 아키텍처·API 목록·파일맵, 개발·장기 로드맵, routing 문서의 검사 전용 설명 |

검사 전용 추적 파일 16개를 삭제했습니다. 기존 9개 검사 API는 등록에서 제거되어 코드 적용 후 404를 반환합니다. `/api/admin/watchdog/scan`은 파일 상태를 점검하는 관리자 기능이므로 유지합니다. 차트 공용 의존성은 모니터링에서도 사용하므로 유지합니다.

아키텍처는 [v1.3.0](architecture-v1.3.0.md)으로 갱신하고 [v1.2.0](archived/architecture-v1.2.0.md)은 보관본으로 이동했습니다. 과거 이슈·설계 기록의 설명은 역사적 기록으로 유지합니다.

## DB·원본 보존 경계

- `ScanResult`, `User.scan_results`, `Client.wpi_scan_id`와 기존 Alembic 이력을 유지합니다. 테이블·컬럼 삭제나 데이터 갱신은 수행하지 않습니다.
- 개인별 응답·점수·해설은 기존 DB의 `scan_result.data` 등에 있을 수 있습니다. 현재 건수·내용·inventory-app 이관 여부는 DB 미접속으로 확인하지 못했습니다. 로컬에서 별도 응답 데이터 덤프는 찾지 못했습니다.
- `docs/wpi/`, `docs/wpis/`, `docs/personal/`, 문항 JSON, 실제 프롬프트는 Git 제외 파일입니다. 일반 clone이나 Git 이력만으로 복구할 수 없으므로 별도 보관·이관 확인 전 삭제 대상에서 제외합니다. 기존 `.gitignore`도 유지합니다.
- inventory-app의 원본 점수 fixture 재생성은 고정 원본 커밋에서 서비스 파일을 추출해 실행하도록 안내를 수정합니다. 실행용 검사 앱에는 새로운 기능·데이터를 추가하지 않습니다.

## 검증

- 프론트엔드 `tsc -b` 및 Vite 운영 빌드 통과.
- 백엔드 인증 경계·채팅 라우팅·검사 제거 테스트 44개 통과. DLL 실행 권한 오류는 자동 승인된 실행으로 해결했고, 테스트 프로세스의 CORS를 `http://localhost:5173`으로 지정했습니다. 초기 스토리지 연결 점검만 대역 처리했으며 실제 FastAPI 라우트의 9개 method/path 404와 OpenAPI 제외 여부를 확인했습니다. 기본 로컬 설정으로 실행한 첫 검사에서는 기존 CORS 기대값 불일치 1건이 발생했습니다.
- Playwright/Chromium으로 1440px 데스크톱·390px 모바일 메뉴, 콘텐츠·대화 기록 이동, 삭제된 5개 화면 경로의 404를 확인했습니다. 인증·목록 응답은 대역이며 운영 계정 로그인이나 실제 데이터 조회는 포함하지 않습니다. 브라우저 실행 오류와 `/api/scan` 요청은 없었습니다.
- 수정 Python 파일 AST 파싱, Compose YAML, `git diff --check` 통과. 독립 리뷰에서 수정이 필요한 결함 없음.
- 고정 원본 커밋과 기존 생성기 로직으로 점수 fixture 50쌍이 모두 재현됨을 파일 쓰기 없이 확인했습니다. 문항 SHA-256, 원본 자료 수량·동일성, 문서 링크도 독립 확인했습니다.
- 운영 배포·실제 DB 대조·새 자료 이관은 수행하지 않았습니다. 원본 자료와 기존 DB·마이그레이션은 보존합니다.
