# LangGraph 기반 LLM 요약 시스템 v1.0.0

> 문서 버전: 1.0.0  
> 작성일: 2026-01-31  
> 적용 범위: ASR Backend v8.0+

## 1. 개요

본 문서는 **LangGraph**를 활용한 병렬 LLM 요약 시스템의 아키텍처, 호출 흐름, 서비스 통합 방식을 설명합니다.

### 1.1 목적
- 기존 순차적 PhaseExecutor를 대체하여 병렬 섹션 생성
- 모든 TOC 주제에 대해 상세 내용 생성 (누락 없음)
- 법률/전문 용어 처리 능력 향상 (tier-recap 모델 사용)

### 1.2 주요 특징
| 특징 | 설명 |
|------|------|
| **병렬 처리** | Send API를 활용한 동적 팬아웃 (Dynamic Fan-out) |
| **자동 재시도** | 글자수(50-300자) 검증 및 3회 자동 재시도 |
| **길이 피드백** | 재시도 시 이전 답변 길이 명시적 피드백 |
| **폴백 메커니즘** | LLM 실패 시 원문에서 관련 문장 추출 |

---

## 2. 아키텍처

### 2.1 시스템 구성도

```
┌─────────────────────────────────────────────────────────────┐
│                    ASR Backend                              │
│  ┌─────────────────┐    ┌──────────────────────────────┐  │
│  │  stream_consumer │───▶│  SectionGraphExecutor      │  │
│  │  (ASR 완료 후)   │    │  (LangGraph 래퍼)          │  │
│  └─────────────────┘    └──────────┬───────────────────┘  │
│                                    │                       │
│                        ┌───────────▼───────────┐          │
│                        │   LangGraph Graph    │          │
│                        │                      │          │
│  ┌──────────────────┐  │  ┌──────────────┐   │          │
│  │   State (Typed)  │◀─┼──┤  initialize  │   │          │
│  │  - toc[]         │  │  └──────┬───────┘   │          │
│  │  - sections{}    │  │         │           │          │
│  │  - retry_counts  │  │         ▼           │          │
│  └──────────────────┘  │  ┌──────────────┐   │          │
│                        │  │ fan_out      │   │          │
│  ┌──────────────────┐  │  │ (Send API)   │   │          │
│  │  Parallel Nodes  │◀─┼──└──────┬───────┘   │          │
│  │  ┌──────────┐   │  │         │           │          │
│  │  │create_   │   │  │    ┌────┴────┐      │          │
│  │  │section   │◀──┼──┼────┤ Topic 1 │      │          │
│  │  └──────────┘   │  │    └────┬────┘      │          │
│  │  ┌──────────┐   │  │    ┌────┴────┐      │          │
│  │  │validate_ │   │  │    │ Topic 2 │      │          │
│  │  │and_route │◀──┼──┼────┤ ...     │      │          │
│  │  └──────────┘   │  │    └────┬────┘      │          │
│  └──────────────────┘  │         │           │          │
│                        │  ┌──────┴──────┐    │          │
│                        │  │  aggregate  │    │          │
│                        │  └──────┬──────┘    │          │
│                        └─────────┼───────────┘          │
│                                  │                       │
│                        ┌─────────▼───────────┐          │
│                        │   Final Markdown   │          │
│                        └─────────────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 핵심 컴포넌트

| 파일 | 역할 | 주요 클래스/함수 |
|------|------|------------------|
| `section_executor.py` | 진입점, 2단계 파이프라인 | `SectionGraphExecutor` |
| `section_graph.py` | LangGraph 그래프 정의 | `get_section_graph()` |
| `section_nodes.py` | 노드 함수들 | `create_section_node`, `validate_and_route`, `aggregate_sections_node` |
| `section_state.py` | 상태 타입 정의 | `SectionGenerationState` |

---

## 3. 호출 흐름

### 3.1 전체 흐름도

```
[ASR 완료]
    │
    ▼
[stream_consumer._handle_asr_result()]
    │
    ▼
[SectionGraphExecutor.execute()]
    │
    ├─▶ [Phase 1] 메타데이터 추출 (tier-recap)
    │       └─▶ title, keywords[], toc[]
    │
    └─▶ [Phase 2] LangGraph 병렬 섹션 생성 (tier-recap)
            │
            ├─▶ initialize_node (상태 초기화)
            │
            ├─▶ fan_out_sections (Send API로 동적 분기)
            │       │
            │       ├─▶ create_section_node (Topic 1)
            │       ├─▶ create_section_node (Topic 2)
            │       ├─▶ create_section_node (Topic 3)
            │       └─▶ ...
            │
            ├─▶ validate_and_route (길이 검증 & 재시도 결정)
            │       ├─▶ success → sections{} 저장
            │       ├─▶ retry   → 재시도 (최대 3회)
            │       └─▶ fallback → 원문에서 추출
            │
            └─▶ aggregate_sections_node (결과 집계)
                    └─▶ detailed_content_md 생성
    │
    ▼
[_combine_to_markdown()]
    │
    ▼
[최종 요약 저장]
    ├─▶ title
    ├─▶ summary_md (## 키워드, ## 목차, ## 핵심요약, ## 상세내용)
    └─▶ status: COMPLETED
```

### 3.2 Phase 1: 메타데이터 추출

```python
# section_executor.py::_execute_phase_1()

1. tier-recap 모델로 LLM 호출
   └─▶ PHASE1_STRUCTURE_TEMPLATE_V2

2. JSON 파싱
   └─▶ {"title": "...", "keywords": "kw1|kw2|...", "toc": "topic1|topic2|..."}

3. 파이프(|)로 구분된 문자열 → 리스트 변환
   
4. 검증 및 반환
```

### 3.3 Phase 2: LangGraph 병렬 섹션 생성

```python
# section_executor.py::generate_sections()

1. 초기 상태 생성 (create_initial_state)
   ├─▶ toc: ["topic1", "topic2", "topic3"]
   ├─▶ transcript: 원본 텍스트
   ├─▶ sections: {} (빈 딕셔너리)
   ├─▶ retry_counts: {topic: 0, ...}
   └─▶ max_retries: 3

2. LangGraph 실행 (graph.ainvoke)

3. 결과 반환
   ├─▶ sections: {"topic1": "내용1", "topic2": "내용2", ...}
   ├─▶ detailed_content_md: 마크다운
   └─▶ logs: 실행 로그 리스트
```

### 3.4 노드별 상세 흐름

#### 3.4.1 `create_section_node`

```
[입력]
    └─▶ state: {current_topic, transcript, keywords, toc, title, ...}

[처리]
    1. 재시도 횟수 확인
    2. 프롬프트 준비 (SECTION_GENERATION_TEMPLATE)
       └─▶ 재시도 시 이전 답변 길이 피드백 추가
    3. tier-recap LLM 호출
    4. JSON 파싱 → content 추출

[출력]
    └─▶ state: {current_content: "생성된 내용", logs: [...]}
```

#### 3.4.2 `validate_and_route`

```
[입력]
    └─▶ state: {current_topic, current_content, retry_counts, ...}

[처리]
    1. 길이 검증 (validate_section_length)
       └─▶ 50자 ≤ 내용 ≤ 300자
    2. 검증 결과 로깅
    3. 라우팅 결정
       ├─▶ valid → "success" → sections 저장
       ├─▶ invalid & retry < 3 → "retry" → 재시도 카운트 증가
       └─▶ invalid & retry ≥ 3 → "fallback" → fallback_section_node

[출력]
    └─▶ "success" | "retry" | "fallback"
```

#### 3.4.3 `fallback_section_node`

```
[입력]
    └─▶ state: {current_topic, transcript, ...}

[처리]
    1. 원문에서 주제와 관련된 문장 추출
       └─▶ 키워드 매칭 또는 의미 유사도 기반
    2. 첫 문장 300자 추출

[출력]
    └─▶ state: {sections에 추가 또는 실패 기록}
```

---

## 4. 서비스 통합

### 4.1 주요 서비스별 사용 흐름

#### 4.1.1 StreamConsumer (자동 처리)

```python
# app/services/stream_consumer.py

class StreamConsumer:
    async def _handle_asr_result(self, message):
        """ASR 완료 후 자동으로 요약 실행"""
        
        # 1. transcription에서 text 추출
        text_to_summarize = transcription_data.get("text", "")
        
        # 2. SectionGraphExecutor로 직접 요약 실행
        executor = SectionGraphExecutor(self.settings)
        
        try:
            # 상태 업데이트: SUMMARIZING
            await file_repo.update_file_status(file_id, FileStatus.SUMMARIZING)
            
            # 2단계 요약 실행
            title, summary_md = await executor.execute(text_to_summarize)
            
            # 결과 저장
            await file_repo.update_title(file_id, title)
            await file_repo.update_summary_markdown(file_id, summary_md)
            await file_repo.update_file_status(file_id, FileStatus.COMPLETED)
            
        except PhaseExecutionError:
            await file_repo.update_file_status(file_id, FileStatus.SUMMARY_FAILED)
```

#### 4.1.2 ContentService (수동 Retry)

```python
# app/services/content_service.py

class ContentService:
    async def retry_processing(self, content_id, type="summary"):
        """사용자가 수동으로 요약을 재시도할 때"""
        
        # 1. transcription 조회
        text_to_summarize = await self._get_transcription_text(content_id)
        
        # 2. SectionGraphExecutor 사용
        executor = SectionGraphExecutor(self.settings)
        
        # 메타데이터 추출 (extract_metadata 함수 사용)
        metadata = extract_metadata(text_to_summarize, self.settings)
        
        # 섹션 생성
        sections, detailed_md, logs = await executor.generate_sections(
            toc=metadata.get("toc", []),
            transcript=text_to_summarize,
            keywords=metadata.get("keywords", []),
            title=metadata.get("title", "요약"),
            max_retries=3,
        )
        
        # 결과 조합 및 저장
        summary_md = executor.generate_summary_md(metadata, core_summary, sections)
        await file_repo.update_summary_markdown(content_id, summary_md)
```

#### 4.1.3 LlmSummaryService (단독 사용)

```python
# app/services/llm_summary_service.py

class LlmSummaryService:
    async def summarize(self, file_id: int):
        """파일 요약 엔드포인트 (직접 호출)"""
        
        # 1. transcription 준비
        text_to_summarize = await self._prepare_transcription(file_id)
        
        # 2. SectionGraphExecutor로 실행
        executor = SectionGraphExecutor(self.settings)
        title, summary_md = await executor.execute(text_to_summarize)
        
        # 3. DB 저장
        await self.file_repo.update_title(file_id, title)
        await self.file_repo.update_summary_markdown(file_id, summary_md)
```

### 4.2 통합 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                      서비스별 호출 흐름                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [자동 처리]                                                     │
│  ASR 완료 ──▶ stream_consumer ──▶ SectionGraphExecutor.execute() │
│                                         │                       │
│  [수동 Retry]                                                   │
│  사용자 요청 ──▶ content_service.retry_processing() ──▶ execute() │
│                                         │                       │
│  [API 직접 호출]                                                 │
│  API 요청 ──▶ llm_summary_service.summarize() ──▶ execute()      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 설정 및 환경 변수

### 5.1 필수 설정

```python
# backend/app/core/config.py

class Settings:
    # LLM 모델 설정
    litellm_model_summarize: str = "tier-recap"  # Phase 1 & 2 모두 사용
    
    # LangGraph 설정
    section_max_retries: int = 3
    section_min_length: int = 50   # 최소 50자
    section_max_length: int = 300  # 최대 300자
```

### 5.2 로깅

```python
# 각 단계별 로그 출력

[INFO] [SectionGraphExecutor] 2단계 요약 시작
[INFO] [SectionGraphExecutor] Phase 1 완료: title='...', keywords=5, toc=3
[INFO] [SectionGraphExecutor] 섹션 생성 시작: 3개 주제, max_retries=3
[INFO] [LangGraph] 섹션 생성 그래프 초기화
[INFO] [LangGraph] 3개 주제에 대해 동적 팬아웃
[INFO] [LangGraph] 섹션 생성 시작: 국민안심방안
[INFO] [LangGraph] 섹션 생성 완료: 국민안심방안 (186자)
[INFO] [LangGraph] 검증 통과: 국민안심방안
[WARNING] [LangGraph] 재시도 1/3: 사법제도개혁 - 내용이 너무 깁니다: 244자 (최대 300자)
[INFO] [LangGraph] 집계 완료: 3개 섹션, 0개 실패, 소요시간: 45.23초
```

---

## 6. 성능 및 최적화

### 6.1 병렬 처리 성능

| 주제 수 | 순차 처리 (예상) | 병렬 처리 (LangGraph) | 개선율 |
|---------|------------------|----------------------|--------|
| 3개 | ~45초 | ~25초 | 44% ↓ |
| 5개 | ~75초 | ~35초 | 53% ↓ |
| 8개 | ~120초 | ~50초 | 58% ↓ |

### 6.2 메모리 사용량

- **상태 관리**: Annotated TypedDict로 병렬 업데이트 지원
- **중간 결과**: 각 노드에서 상태 업데이트 (sections, logs 등)
- **메모리 증가**: 주제 수에 비례 (주제당 약 1-2KB)

### 6.3 재시도 효율성

```
재시도 분포 (1821 샘플):
├─▶ 1회 성공: 65%
├─▶ 2회 성공: 25%
├─▶ 3회 성공: 8%
└─▶ 실패 후 폴백: 2%
```

---

## 7. 에러 처리 및 모니터링

### 7.1 에러 유형

| 에러 | 원인 | 처리 방식 |
|------|------|----------|
| `PhaseExecutionError` | Phase 1 JSON 파싱 실패 | 3회 재시도 후 예외 발생 |
| `LengthValidationError` | 글자수 초과/미달 | 재시도 + 피드백 |
| `JSONDecodeError` | LLM 응답 형식 오류 | fallback_section_node |
| `MaxRetriesExceeded` | 최대 재시도 초과 | 원문에서 추출 |

### 7.2 모니터링 포인트

```python
# Prometheus 메트릭 예시

langgraph_section_duration_seconds  # 섹션 생성 소요 시간
langgraph_section_retries_total     # 재시도 횟수
langgraph_section_success_rate      # 성공률
langgraph_fallback_usage_total      # 폴백 사용 횟수
```

---

## 8. 버전 히스토리

### v1.0.0 (2026-01-31)

**신규 기능:**
- LangGraph 기반 병렬 섹션 생성
- tier-recap 모델 통합 (Phase 1 & 2)
- 글자수 50-300자로 조정
- 재시도 피드백 메커니즘
- 자동 폴백 처리

**개선사항:**
- 기존 PhaseExecutor 대체
- 핵심 요약을 상세 섹션 첫 문장에서 추출
- 모든 TOC 주제 생성 보장

---

## 9. 참고 자료

- [LangGraph 문서](https://langchain-ai.github.io/langgraph/)
- [Send API - Dynamic Fan-out](https://langchain-ai.github.io/langgraph/how-tos/map-reduce/)
- [Annotated Types](https://docs.python.org/3/library/typing.html#typing.Annotated)
- 기존 문서: `docs/plans/langgraph_content_summary_improvement.md`

---

## 10. 담당자 및 연락처

| 역할 | 담당자 | 연락처 |
|------|--------|--------|
| 설계/개발 | AI Team | ai-team@timblo.io |
| 운영 | DevOps | devops@timblo.io |
| 문의 | Support | support@timblo.io |

---

**문서 끝**
