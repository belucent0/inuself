# TSMC 관련 대화 검색 품질 평가 보고서

**작성일**: 2026-02-06
**대화 ID**: `30a55732-04c1-48e2-9449-56bd00958188`
**분석 대상**: 연이은 3개의 질문

---

## 1. 질문 시퀀스 분석

### 질문 1: "tsmc의 2026 ai 붐상황에서의 투자 계획은"
- **시간**: 00:30:49
- **응답 길이**: 1,936자
- **모드**: SEARCH

**평가**:
- ✅ **구체성**: 높음 (TSMC, 2026, AI 붐, 투자 계획)
- ✅ **검색 가능성**: 매우 좋음 (구체적 키워드 다수)
- ✅ **독립성**: 완전히 독립적인 질문 (맥락 불필요)
- **예상 검색 쿼리**:
  - 원본: "tsmc의 2026 ai 붐상황에서의 투자 계획은"
  - HyDE: "TSMC 2026 AI 반도체 투자 계획 설비 확대"
  - 키워드: "TSMC 2026 AI 투자"

### 질문 2: "구체적인 투자 계획에 대한 안내"
- **시간**: 00:32:47 (질문 1 후 1분 58초)
- **응답 길이**: 1,855자
- **모드**: SEARCH

**평가**:
- ⚠️ **컨텍스트 의존성**: 높음 ("구체적인 투자 계획" = TSMC의 투자 계획)
- ⚠️ **독립성**: 낮음 (이전 질문 참조 필수)
- ❌ **검색 가능성**: 낮음 (주체가 누락됨)
- **필요한 Contextualization**:
  - 원본: "구체적인 투자 계획에 대한 안내"
  - 재작성 필요: "TSMC의 2026 AI 붐 상황에서의 구체적인 투자 계획"
- **문제점**:
  - "투자 계획"만으로 검색하면 일반적인 투자 가이드가 나옴
  - TSMC 맥락이 누락되면 관련 없는 결과 반환 가능

### 질문 3: "TSMC의 사례를 물은 겁니다"
- **시간**: 00:34:42 (질문 2 후 1분 55초)
- **응답 길이**: 686자
- **모드**: SEARCH

**평가**:
- ❌ **의도 명확화**: 사용자가 AI 응답이 의도와 다름을 지적
- ❌ **컨텍스트 의존성**: 매우 높음 (무엇의 "사례"인지 불명확)
- ❌ **검색 가능성**: 매우 낮음
- **문제 분석**:
  - 질문 2에서 AI가 TSMC가 아닌 일반적인 투자 계획(국세청 제도 등)을 답변
  - 사용자가 피드백 제공하며 의도 재설정
- **필요한 Contextualization**:
  - 원본: "TSMC의 사례를 물은 겁니다"
  - 재작성 필요: "TSMC의 성공적인 투자 사례나 전략"

---

## 2. 검색 품질 문제점

### 2.1 메타데이터 부재
**문제**: 구버전 대화라서 다음 정보가 저장되지 않음
- ❌ Intent 분석 결과 (`query_analysis`)
- ❌ 생성된 검색 쿼리 목록 (`search_queries`)
- ❌ 검색 결과 및 품질 점수 (`search_results`)
- ❌ Citation 정보 (`citations`)
- ❌ 사고 과정 (`thinking_steps`)

**영향**:
- 실제 어떤 검색 쿼리가 생성되었는지 확인 불가
- Query Contextualization이 작동했는지 검증 불가
- 검색 결과의 품질 점수 분석 불가

**해결**:
- ✅ `backend/app/controllers/ai_chat_controller.py` 수정 완료
- → 새 대화에서는 모든 메타데이터 저장됨

### 2.2 Query Contextualization 성능 의문

**의심 시나리오 1**: Contextualization이 작동하지 않음
```python
# 질문 2: "구체적인 투자 계획에 대한 안내"
# 예상: "TSMC의 2026 AI 투자에 대한 구체적인 계획"
# 실제: "구체적인 투자 계획" (그대로?) → 일반적인 투자 안내 결과 반환
```

**의심 시나리오 2**: 검색 엔진이 컨텍스트 쿼리를 제대로 처리하지 못함
```python
# 재작성은 되었지만, 검색 결과가 여전히 관련 없음
# 예: "TSMC 2026 AI 투자 구체적 계획" 검색 → 관련 자료 부족
```

**의심 시나리오 3**: 검색 결과 랭킹 문제
```python
# 관련 결과는 있지만, 품질 점수가 낮아 하위권으로 밀림
# 또는 일반적인 투자 안내가 더 높은 점수를 받음
```

### 2.3 응답 생성 문제

**질문 2 응답 분석**:
- 응답에 "국세청 투자확대 계획서" 같은 무관한 내용 포함
- TSMC 투자 정보(82조원)는 있지만, 구체적 세부 계획 부족
- 사용자가 만족하지 못해 질문 3에서 재요청

**원인 가능성**:
1. 검색 쿼리가 너무 일반적 ("투자 계획")
2. 검색 결과에 일반 투자 가이드가 상위권에 포함됨
3. Generator가 무관한 소스를 인용함

---

## 3. 기술적 검증 사항

### 3.1 ContextualizeTransformer 구현 확인
**위치**: `backend/app/agents/nodes/intent_parser.py:149-249`

```python
class ContextualizeTransformer(QueryTransformer):
    async def transform(self, query: str, state: GraphState, settings: Any) -> list[str]:
        # 최근 3턴(6개 메시지) 사용
        recent_messages = messages[-6:]

        # LLM으로 쿼리 재작성
        context_prompt = f"""이전 대화:
        {conversation_context}

        현재 질문: "{query}"

        현재 질문이 이전 대화를 참조하고 있다면,
        독립적으로 이해 가능한 완전한 질문으로 재작성하세요.
        """
```

✅ **구현 상태**: 완전히 구현됨
⚠️ **적용 여부**: 로그 없이 확인 불가

### 3.2 Transformer 적용 순서
**위치**: `backend/app/agents/nodes/intent_parser.py:522-527`

```python
self.transformers: list[QueryTransformer] = [
    ContextualizeTransformer(),  # 1순위: 대화 맥락 반영
    DecomposeTransformer(),      # 2순위: 질의 분해
    HyDETransformer(),           # 3순위: HyDE (Phase 1B)
]
```

✅ **순서**: 올바름 (Contextualization이 최우선)

### 3.3 호출 지점 확인
**위치**: `backend/app/agents/nodes/intent_parser.py:565-568`

```python
if current_mode in (AIMode.SEARCH, AIMode.HYBRID):
    query_analysis = await self.reformulate_query(query, state)
    if query_analysis:
        search_queries = query_analysis.get("sub_queries", [query])
```

✅ **호출**: SEARCH 모드에서 정상 호출됨

### 3.4 로그 확인 지점
**로그 라인**:
- `[ContextualizeTransformer] '{query}' → '{contextualized}'` (212번 라인)
- `[IntentParser] {transformer.__class__.__name__} applied: {len(transformed)} queries` (799번 라인)

---

## 4. 평가 요약

### 구현 완성도
| 기능 | 상태 | 비고 |
|------|------|------|
| Query Contextualization | ✅ 구현 완료 | ContextualizeTransformer |
| HyDE Transformation | ✅ 구현 완료 | HyDETransformer |
| Query Decomposition | ✅ 구현 완료 | DecomposeTransformer |
| Vector Search | ✅ 구현 완료 | Phase 2 |
| Quality Ranking | ✅ 구현 완료 | Phase 3 |
| Citation System | ✅ 구현 완료 | Phase 4 |
| 메타데이터 저장 | ✅ 수정 완료 | V8.3 업데이트 |

### 검증 필요 사항
| 항목 | 우선순위 | 방법 |
|------|----------|------|
| Contextualization 실제 작동 여부 | 🔴 높음 | 새 대화 테스트 + 로그 확인 |
| 검색 쿼리 품질 | 🔴 높음 | 메타데이터 분석 |
| 검색 결과 관련성 | 🟡 중간 | 품질 점수 분포 확인 |
| Citation 사용 여부 | 🟢 낮음 | 응답 텍스트 분석 |

---

## 5. 추천 액션

### 즉시 실행
1. **새 대화 테스트 생성**
   ```bash
   # 컨텍스트 의존적인 질문 시퀀스
   - "파이썬 웹 프레임워크 비교해줘"
   - "그 중에서 가장 빠른 건?"  ← 맥락 참조
   - "그거 배우려면 얼마나 걸려?"  ← 맥락 참조
   ```

2. **메타데이터 확인**
   ```bash
   uv run python scripts/analyze_recent_chat.py
   ```
   - `intent` 필드 확인
   - `search_queries` 배열 확인 (원본 vs 재작성)
   - `search_results` 품질 점수 확인

3. **로그 모니터링**
   ```bash
   # 백엔드 실행 시 로그 확인
   [ContextualizeTransformer] '그거 배우려면?' → '파이썬 웹 프레임워크 배우려면?'
   ```

### 중기 개선
1. **검색 품질 평가 메트릭 추가**
   - Query-Result Relevance Score
   - Citation Coverage Rate
   - Context Preservation Rate

2. **Contextualization 품질 향상**
   - Few-shot 예제 추가
   - 대화 히스토리 요약 개선
   - 엔티티 추적 (Entity Tracking)

3. **사용자 피드백 수집**
   - "이 답변이 도움이 되었나요?" 버튼
   - 검색 결과 관련성 평가

---

## 6. 결론

### 현재 상태
- ✅ **기술적 완성도**: 모든 Phase 구현 완료 (1B~4)
- ❌ **검증 완료**: 메타데이터 부재로 실제 작동 확인 불가
- ⚠️ **품질 의문**: 질문 2, 3에서 사용자 불만족

### 핵심 문제
**"Query Contextualization이 실제로 작동하는가?"**
- 구현: ✅ 완료
- 적용: ❓ 미확인 (로그 부재)
- 효과: ❓ 미검증 (메타데이터 부재)

### 다음 단계
1. 새 테스트 대화 생성 (컨텍스트 의존적 질문 시퀀스)
2. 수정된 메타데이터 저장 검증
3. 검색 쿼리 재작성 결과 분석
4. 검색 품질 개선 필요시 추가 튜닝

---

**작성자**: Claude (AI Agent)
**참고 파일**:
- `backend/app/agents/nodes/intent_parser.py` (IntentParser, Transformers)
- `backend/app/controllers/ai_chat_controller.py` (메타데이터 저장)
- `scripts/analyze_tsmc_chat.py` (분석 스크립트)
