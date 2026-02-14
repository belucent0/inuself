# AI Chat Search Quality Tuning

## 목적

- HYBRID/SEARCH 모드에서 품질과 응답속도의 균형을 맞춘다.
- 튜닝 후보를 동일한 질문셋으로 비교해 운영값을 결정한다.

## 주요 파라미터

- `AGENT_SEARCH_WEB_LIMIT`: SEARCH 모드 웹 결과 수집량 (권장 12~20)
- `AGENT_HYBRID_WEB_LIMIT`: HYBRID 모드 웹 결과 수집량 (권장 8~12)
- `AGENT_CONTENT_FETCH_TOP_K`: 2차 리랭킹용 본문 fetch 개수 (권장 6~10)

기본값은 다음과 같다.

- `AGENT_SEARCH_WEB_LIMIT=15`
- `AGENT_HYBRID_WEB_LIMIT=10`
- `AGENT_CONTENT_FETCH_TOP_K=8`

## 추천 실험 시나리오

### 후보 A (속도 우선)

- `AGENT_HYBRID_WEB_LIMIT=8`
- `AGENT_CONTENT_FETCH_TOP_K=8`

### 후보 B (균형)

- `AGENT_HYBRID_WEB_LIMIT=10`
- `AGENT_CONTENT_FETCH_TOP_K=8`

### 후보 C (품질 우선)

- `AGENT_HYBRID_WEB_LIMIT=10`
- `AGENT_CONTENT_FETCH_TOP_K=10`

## 실행 절차

1. 후보별로 `.env` 값을 바꾼다.
2. 각 후보마다 백엔드를 재빌드/재시작한다.
   - `docker compose up -d --build backend`
3. 동일 질문셋으로 대화를 생성한다.
4. 품질 리포트를 생성한다.
   - `python scripts/run_quality_test.py --limit 20 --source redis --output-dir quality_test_reports/<candidate_label>`
5. 후보를 비교한다.
   - 1:1 비교: `python scripts/compare_quality_reports.py --baseline <A.json> --candidate <B.json>`
   - 다중 랭킹: `python scripts/rank_quality_candidates.py --report A=<A.json> --report B=<B.json> --report C=<C.json>`

## 의사결정 기준

- 우선순위 1: `SearchQuality avg_score` 상승
- 우선순위 2: `avg_content_coverage_ratio` 상승
- 우선순위 3: `avg_retry_count` 감소
- 보조지표: `content_enrichment_rate`, `avg_second_stage_score`

지연시간 데이터를 함께 볼 수 있으면 아래처럼 함께 평가한다.

- `python scripts/rank_quality_candidates.py --report A=<A.json> --report B=<B.json> --latency A=1450 --latency B=1820`

## 운영 체크 로그

재시작 후 아래 로그가 찍히면 새 파라미터가 적용된 상태다.

- `[Searcher] Options: limit=..., content_fetch_top_k=...`
- `[Searcher] Content enrichment complete: X/Y fetched`

## 참고: 대화 0개로 나올 때

- 현재 저장 구조는 legacy `ai:conversations`가 아니라 `ai:thread:*` 캐시를 사용한다.
- `run_quality_test.py`는 이제 `ai:thread:*`를 자동 인식한다.
- 로컬 의존성 차이로 PostgreSQL 로더가 실패할 수 있으므로, 우선 `--source redis`로 실행하는 것을 권장한다.
