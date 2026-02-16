# WPI AI 리포트 품질 회고 및 재설계 리서치 보고서

**날짜**: 2026-02-15  
**상태**: 분석 완료, 재설계 실행안 합의 대기

## 1) 이 문서의 목적

이 문서는 다음을 한 번에 정리한다.

1. 우리가 최근 WPI AI 리포트 품질 문제를 어떻게 다뤄왔는지 (작업 맥락/히스토리)
2. 현재(AS-IS) 파이프라인이 어떤 방식으로 동작하는지
3. 그럼에도 품질이 낮아지는 구조적 원인이 무엇인지
4. HCI/XAI/감성컴퓨팅 리서치를 기준으로 어떤 재설계가 타당한지
5. 그래프 DB / 벡터 DB 도입 시 기대효과와 트레이드오프

신규 팀원이 이 문서만 읽어도 "왜 이 작업을 시작했는지, 어디서 막혔는지, 무엇을 다음으로 해야 하는지"를 이해하도록 작성했다.

---

## 2) 지금까지의 상황 요약 (현장 맥락)

### 2.1 우리가 하고 있던 작업

- 목표: WPI 검사 결과를 사용자 맞춤형으로 상세 해석하는 AI 리포트 품질 향상
- 초기 상태: 단일 프롬프트 기반 생성이라 근거 밀도/일관성 부족
- 개선 시도:
  - LangGraph 기반 섹션 생성/검증 파이프라인 도입
  - 큐잉/상태 전이(`queued -> processing -> completed|failed`) 안정화
  - stuck 작업 자동 실패 전환 및 재큐잉 복구 로직 도입
  - 오프라인 품질평가 스크립트(`scripts/generate_wpi_quality_report.py`) 도입

### 2.2 실제 운영에서 봉착한 문제

- 동일 호스트에서 compose project 혼재(`torch-test` vs `torch-test-langfuse`)로 런타임 불일치
- provider busy(LiteLLM 경유)로 장시간 대기, 완료 이벤트 누락 가능성
- 일부 시점에 WPI 문항 파일 누락으로 `GET /api/scan/history` 500 발생

관련 이슈 기록:

- `docs/issues/260214-scan-history-500-missing-wpi-question-files.md`
- `docs/issues/260215-dynamic-model-override-handoff.md`

---

## 3) 현재 시스템(AS-IS)은 어떻게 동작하는가

## 3.1 API -> 서비스

- 핵심 서비스: `backend/app/services/wpi_service.py`
- 주요 책임:
  - I/Me 점수 계산 및 gap 분석 (`calculate_scores`, `calculate_gap_analysis`)
  - 리포트 입력 컨텍스트 조립 (`_build_ai_report_messages`)
  - 상태 조회/복구 (`_reconcile_ai_report_state`)
  - 큐 등록 (`enqueue_ai_report_generation`)

리포트 입력 컨텍스트(핵심 필드):

- `i_test`: dominant_type, scores, top_types
- `me_test`: dominant_type, scores, top_types
- `gap_analysis`, `gap_highlights`
- `score_insights` (dominant margin, secondary type)
- `auto_profile`, `secondary_profiles`

프롬프트 계약:

- `backend/app/prompts/wpi_report.py`
- 6개 섹션, 최소 길이/불릿/소제목 규칙을 명시

## 3.2 큐 -> 워커

- 태스크: `worker/tasks/wpi_report_task.py`
- 프로세서: `worker/processors/wpi_report_processor.py`

동작 방식:

1. started 이벤트 발행
2. 가능하면 LangGraph 경로 실행
3. LangGraph 실패 시 single-prompt fallback
4. 결과를 storage에 업로드 후 completed 이벤트 발행
5. 실패 시 failed 이벤트 발행

## 3.3 LangGraph 생성/검증

- 실행기: `worker/processors/wpi_report_graph_executor.py`
- 그래프 노드:
  - `plan -> generate -> validate -> retry -> assemble`
- 검증 항목(현재):
  - 섹션 존재
  - 최소 문자 수
  - 최소 불릿 수
  - actions 섹션의 `### 개인 실행` / `### 협업/소통` 하위 불릿 개수

중요한 사실:

- 현재 검증은 **구조적 품질**에 강함
- **의미/수치 정합성** 검증은 런타임 생성 경로에서 강제되지 않음

---

## 4) 왜 품질이 낮게 느껴지는가 (근본 원인)

아래는 구조적 원인이다. 단순 프롬프트 문구 문제가 아니다.

### 원인 A. 구조 검증 중심, 의미 검증 부족

- 현재 그래프 검증은 길이/불릿/형식 위주
- "점수/갭 계산이 문장에 정확히 반영됐는지"를 런타임에서 강제하지 않음
- 결과: 겉보기 형식은 좋아졌지만 숫자 불일치(예: 갭 오기입)가 발생 가능

### 원인 B. 근거-주장 연결(traceability) 부재

- 문장마다 어떤 점수/축/프로파일 스니펫을 근거로 썼는지 추적 데이터가 없음
- 결과: 사람이 읽을 때 "그럴듯하지만 왜 그런지"가 약해짐

### 원인 C. 자산은 있으나 구조화가 약함

- `wpi_profile_parser`는 `basic_need/strengths/weaknesses/personality_description/me_context_analysis`를 로딩함
- 즉 설명 자산 자체는 존재
- 하지만 생성 단계에서 이를 `claim-evidence` 단위로 강제하지 않아 활용 편차 발생

### 원인 D. 운영 불안정이 품질 체감을 악화

- compose 충돌, provider busy, 이벤트 누락은 사용자 체감 품질을 크게 낮춤
- 품질 자체 + 신뢰성(완주/일관 응답) 문제가 중첩됨

---

## 5) 우리가 이미 시도한 해결책과 효과

### 효과 있었던 것

1. LangGraph 섹션화 + 리트라이
   - 최소 분량/포맷 준수율 개선
2. stuck 복구 로직
   - 영구 `processing` 고착을 `failed`로 자동 정리하여 재시도 가능
3. deterministic 품질 스크립트
   - `scripts/generate_wpi_quality_report.py`
   - 구조/길이/불릿/actions/근거비율/수치정합성/금지표현 점검 가능

### 아직 부족한 것

1. 온라인 생성 경로에 수치 정합성 게이트가 약함
2. 근거 추적 JSON(trace) 저장 없음
3. 품질 스크립트는 오프라인 중심(운영 path와 완전 결합 전)

---

## 6) 리서치 인사이트 (HCI/XAI/감성컴퓨팅)

핵심 메시지: "개인화" 자체보다 "검증 가능한 설명"이 품질의 본질이다.

대표 참고 축:

- Human-centered XAI 평가 프레임 (신뢰/이해/사용성/협업 성능)
- 개인화 설명의 효과/한계(세밀 personalization이 항상 성능 향상으로 이어지지 않음)
- 감성컴퓨팅 개인화 taxonomy (data-level vs model-level personalization)
- human-in-the-loop/claim verification 기반 보고 생성

실무로 번역하면:

1. 사용자 맞춤 문체보다 먼저 근거 일관성 확보
2. claim-evidence 연결을 기계검증 가능하게 저장
3. 온라인/오프라인 평가를 같은 기준으로 통합

---

## 7) 앞으로의 재설계 제안 (TO-BE)

## 7.1 설계 원칙

1. **Fact-first**: 숫자/축/우세/보조형은 결정론적 계산값만 사용
2. **Evidence-bound**: 문장 생성은 근거 ID를 반드시 참조
3. **Fail-closed**: 정합성 실패 시 재생성 또는 실패 처리
4. **Auditable**: 최종 리포트와 함께 trace 저장

## 7.2 권장 아키텍처 (점진적)

### Phase 1 (현 스택 내)

- `wpi_report_graph_executor`에 의미 검증 노드 추가
  - 숫자 일치(갭/차이 계산)
  - 근거 커버리지(문장 대비 근거 문장 비율)
  - 비우세형 반영 최소조건
- 결과 저장 시 `trace_json`(claim->fact/snippet 매핑) 추가

### Phase 2 (데이터 재분류)

- 자산을 아래 구조로 재분류
  - `type_glossary` (I 5 + Me 5)
  - `axis_glossary` (갭 축별 해석 규칙)
  - `profile_snippets` (조합/상황별 근거 스니펫)
  - `action_templates` (행동 제안 템플릿)

### Phase 3 (검색 계층 고도화)

- Hybrid retrieval 고려:
  - Graph: 관계/제약/추적
  - Vector: 유사 근거 회수
  - Generator: claim별 evidence id 강제

---

## 8) 신규 도입안 트레이드오프

## 8.1 Vector DB 중심

- 장점:
  - 도입 빠름, 텍스트 유사도 개선 즉효
- 단점:
  - 관계 추론/근거 추적/감사 가능성 약함
  - 다중-hop 설명 일관성에 한계

## 8.2 Graph DB 중심

- 장점:
  - 관계 기반 추론, claim-evidence 추적, 규칙 강제에 강함
- 단점:
  - 스키마 설계/운영 복잡도 증가
  - 비정형 텍스트 회수력 보완 필요

## 8.3 Hybrid (Graph + Vector)

- 장점:
  - 정합성(그래프) + 회수력(벡터) 동시 확보
  - 보고서 감사 가능성/설명 가능성 강화
- 단점:
  - 초기 구축 비용과 운영 복잡도 상승
  - 파이프라인 관측/장애 대응 체계 필요

현 시점 권장:

- 단기: 현 스택에서 의미 검증/trace 저장 강화
- 중기: 데이터 재분류 + Hybrid 검색 계층 도입

---

## 9) 실행 로드맵 (권장)

1. **2주**: 온라인 의미검증 노드 + trace_json 저장
2. **2~4주**: glossary/snippet 데이터 모델 재분류
3. **4~6주**: Hybrid PoC (Graph + Vector) + 품질 A/B
4. **상시**: 품질 스크립트 결과를 PR 기준으로 운영(현재는 수동 실행)

품질 KPI 예시:

- 숫자 정합성 오류율
- 근거-주장 연결률
- 섹션 계약 준수율
- 사용자 피드백(이해도/실행가능성)

---

## 10) 참고 자산 (내부)

- 파이프라인/프롬프트
  - `backend/app/services/wpi_service.py`
  - `backend/app/prompts/wpi_report.py`
  - `worker/processors/wpi_report_processor.py`
  - `worker/processors/wpi_report_graph_executor.py`
  - `worker/tasks/wpi_report_task.py`

- 품질 평가
  - `scripts/generate_wpi_quality_report.py`
  - `.ci/quality/wpi_quality_rules.json`
  - `.ci/quality/fixtures/wpi_report_quality_cases.json`

- 맥락/운영 이슈
  - `docs/issues/260214-scan-history-500-missing-wpi-question-files.md`
  - `docs/issues/260215-dynamic-model-override-handoff.md`
  - `docs/personal/04-wpi-combination-types.md`

---

## 11) 결론

현재 품질 저하는 "모델이 못해서"가 아니라, **구조적 검증 체계가 의미 정합성까지 닿지 못한 설계 문제**에 가깝다.

이미 섹션 구조화/큐 안정화/오프라인 평가 기반은 확보했다. 이제 필요한 것은:

1. 런타임 의미검증 강화
2. 근거 추적(traceability) 내장
3. 데이터 재분류와 검색 계층 고도화(Hybrid)

이 3가지를 순차적으로 적용하면, 리포트의 "길이"가 아니라 "신뢰 가능한 해석 품질"을 올릴 수 있다.

---

## 12) 외부 리서치 근거와 설계 시사점

아래는 이번 조사에서 품질 개선 설계에 직접적으로 연결된 레퍼런스들이다.

### 12.1 HCI / XAI 평가 프레임

1. Rong et al., *Towards Human-centered Explainable AI: A Survey of User Studies for Model Explanations* (TPAMI, 2024)
   - 핵심: XAI 품질을 trust/understanding/usability/human-AI collaboration 관점으로 평가
   - 시사점: WPI 리포트도 "문장 품질"만이 아니라 "사용자 이해/행동 변화" KPI가 필요

2. Amershi et al., *Guidelines for Human-AI Interaction* (CHI, 2019)
   - 핵심: 설명 타이밍/불확실성 표기/사용자 제어권이 실사용 품질을 좌우
   - 시사점: 리포트 UI/후속 행동 유도까지 포함한 설계가 필요

3. Doshi-Velez & Kim, *Towards a Rigorous Science of Interpretable Machine Learning* (2017)
   - 핵심: 해석가능성은 알고리즘 지표 + 인간 대상 실험 지표를 함께 봐야 함
   - 시사점: 오프라인 스크립트 점수와 사용자 피드백 메트릭을 동시 운영

### 12.2 개인화 설명의 효과/한계

4. Nimmo et al., *User Characteristics in Explainable AI: The Rabbit Hole of Personalization?* (2024)
   - 핵심: 세밀 personalization이 항상 이해도/신뢰 개선으로 이어지지 않음
   - 시사점: 무제한 개인화보다 세그먼트 기반 적응형 개인화가 안전

5. Bahel et al., *Personalizing explanations ... cognitive abilities* (2024)
   - 핵심: 특정 사용자군에서는 설명 개인화가 실제 이해 성능을 개선
   - 시사점: "누구에게 어떤 설명을 얼마나"를 분기하는 정책이 필요

### 12.3 감성컴퓨팅 개인화

6. Li et al., *Beyond One-Size-Fits-All: A Survey of Personalized Affective Computing in Human-Agent Interaction* (2023/2026)
   - 핵심: personalization을 data-level / model-level로 나눠 설계해야 함
   - 시사점: WPI 데이터도 trait/state/evidence/action 계층 재분류가 유효

7. Han et al., *Systematic Evaluation of Personalized Deep Learning Models for Affect Recognition* (IMWUT, 2024)
   - 핵심: personalization 성능은 데이터량/적응전략/재현성 관리에 크게 의존
   - 시사점: 운영 전 A/B와 재현 가능한 벤치마크가 필수

### 12.4 근거 추적형 생성 / Graph+Vector

8. Microsoft GraphRAG docs + paper (`arXiv:2404.16130`)
   - 핵심: 그래프 기반 global/local retrieval로 다중-hop 근거 결합 강화
   - 시사점: 단순 벡터 검색보다 관계 기반 근거 조합에 유리

9. TRACE (`arXiv:2406.11460`) 및 CoE/E2G 계열 (`arXiv:2401.05787`)
   - 핵심: claim-evidence 경로를 분리/명시할수록 정합성 향상
   - 시사점: report_md와 trace_json 동시 저장 구조가 필요

10. VeriTrail/Claimify (Microsoft Research)
   - 핵심: 생성 후 claim 추출 + 근거 역검증 파이프라인이 hallucination 방지에 유효
   - 시사점: 런타임 검증 게이트에 claim 검증 노드 도입 가치 높음

---

## 13) 옵션별 의사결정 매트릭스 (요약)

| 옵션 | 품질 기대치 | 구현 난이도 | 운영 복잡도 | 설명가능성/감사성 | 권장 시점 |
|------|-------------|-------------|-------------|-------------------|------------|
| 현재 구조 + 의미검증 강화 | 중상 | 낮음~중간 | 낮음 | 중간 | 즉시 |
| Vector DB 중심 확장 | 중상 | 중간 | 중간 | 중간(제한적) | 단기 |
| Graph DB 중심 확장 | 중상~상 | 중상~높음 | 높음 | 높음 | 중기 |
| Graph + Vector Hybrid | 높음 | 높음 | 높음 | 매우 높음 | 중기~장기 |

해석:

- 단기 성과는 "현 구조 의미검증 강화"가 가장 빠르다.
- 최종 품질/감사성을 목표로 하면 Hybrid가 가장 유리하나, 운영/스키마 비용이 크다.

---

## 14) 현재 우리가 실제로 쓰는 방법(요약)

현재는 아래 방식으로 동작한다.

1. 백엔드에서 점수/갭/auto_profile 컨텍스트를 만들어 프롬프트 구성
2. 워커에서 LangGraph 섹션 생성(실패 시 single prompt fallback)
3. 섹션 검증은 형식 중심(길이/불릿/소제목)
4. 완료 이벤트로 결과 반영
5. 별도 오프라인 스크립트로 구조+수치 정합성 품질 평가

즉, 생성 경로는 "형식 안정화"가 중심이고, 의미 정합성은 아직 보조적으로 검증 중이다.

---

## 15) 외부 근거 상세 표 (Method / Metric / WPI 적용성)

| 구분 | 레퍼런스 | Method (요약) | Metric (핵심) | WPI 리디자인 적용 포인트 |
|------|----------|---------------|---------------|---------------------------|
| XAI | Ribeiro et al., 2016 (LIME) | 인스턴스 주변 로컬 대리모델로 설명 생성 | local fidelity, sparsity | 문장별 "왜 이 해석인가"를 로컬 근거로 표시 |
| XAI | Ribeiro et al., 2018 (Anchors) | 조건 규칙(anchor) 기반 설명 | anchor precision, coverage | "이 조건에서만 유효"한 해석 범위 명시 |
| XAI | Lundberg & Lee, 2017 (SHAP) | Shapley 기반 일관 기여도 산출 | local accuracy, consistency | 유형/축 기여도 수치화 및 비교 일관성 강화 |
| HCI/XAI | Doshi-Velez & Kim, 2017 | 해석가능성 평가 3계층 프레임 | task performance, human-grounded eval | 오프라인 점수 + 사용자 과제 성능을 함께 관리 |
| HCI | Amershi et al., CHI 2019 | Human-AI interaction 가이드라인 | task success, workload, satisfaction | 결과 표시 방식/경고/후속행동 UX 기준 수립 |
| HITL | Kulesza et al., 2015 | explanatory debugging (사용자 수정 루프) | 이해도 향상, 수정 효율 | 리포트 피드백 반영 루프(수정 후 재생성) |
| Personalization | Poursabzi-Sangdeh et al., 2018/2021 | 해석 복잡도/투명성 사용자 실험 | simulatability, intervention quality | 과도한 개인화/복잡화 방지, 단계별 설명 제공 |
| Personalization | Antognini et al., IJCAI 2021 | critique 기반 개인화 설명 업데이트 | 사용자 선호/정확도 개선 | 사용자 반응 기반 설명 깊이 자동 조정 |
| Fairness | Hardt et al., 2016 | Equality of Opportunity | group TPR gap | 특정 집단에 불리한 해석 패턴 모니터링 |
| Fairness | Kusner et al., 2017 | Counterfactual Fairness | counterfactual consistency | 민감 속성 변화 시 설명 결과 안정성 점검 |
| Governance | Mitchell et al., 2019 (Model Cards) | 모델 리포팅 표준 템플릿 | disclosure completeness | 리포트 엔진 한계/적용 범위/금지 사용 사례 문서화 |
| Affective XAI | Guerdan et al., 2021 | affect 신호 기반 설명 적응 | AU/arousal 기반 실패 신호 | 사용자의 혼란 징후에 설명 난이도/길이 동적 조정 |

---

## 16) 내부 자산 인벤토리 (신규 팀원 온보딩용)

### 16.1 WPI 품질 평가 핵심 파일

- `.ci/quality/wpi_quality_rules.json`
  - 섹션 규칙/가중치/금지표현/합격 기준 정의
- `.ci/quality/fixtures/wpi_report_quality_cases.json`
  - good/bad fixture로 회귀 테스트 기준점 제공
- `scripts/generate_wpi_quality_report.py`
  - deterministic 평가 실행기 (`--strict` 지원)

### 16.2 운영/품질 이슈 문서

- `docs/issues/260214-scan-history-500-missing-wpi-question-files.md`
  - 이력 500 장애 원인/복구/재발방지
- `docs/issues/260215-dynamic-model-override-handoff.md`
  - 멀티 워크트리/compose 충돌 주의 및 운영 체크리스트

### 16.3 도메인 자산 문서

- `docs/personal/04-wpi-combination-types.md`
  - 25개 조합유형 자산 구조와 파싱 전략(설명 데이터 재분류 근거)

---

## 17) 최종 의사결정 제안

1. **즉시(현재 스택)**: 런타임 의미검증 노드 + trace_json 저장
2. **단기(2~4주)**: 설명 자산 재분류(`type_glossary`, `axis_glossary`, `profile_snippets`)
3. **중기(4~6주)**: Graph+Vector Hybrid PoC 및 품질 A/B
4. **운영 전환 기준**:
   - 숫자 정합성 오류율 임계치 이하
   - 근거-주장 연결률 임계치 이상
   - 사용자 이해도/실행가능성 지표 개선

요약하면, 지금 필요한 것은 "더 긴 문장"이 아니라 **검증 가능한 근거 중심 생성체계**다.
