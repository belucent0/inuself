# FLM 추론 모델 스트리밍 문제 해결

**날짜**: 2026-01-24
**상태**: 해결됨

## 증상

- 추론 모드(qwen3-tk:4b)로 웹 검색 시 UI에서 추론 과정이 표시되지 않음
- NPU는 활발히 작동하지만 UI에는 아무 응답 없음
- 수 분 후 최종 결과만 표시됨

## 근본 원인

### 1. FLM 서버의 스트리밍 응답 구조

FLM 추론 모델은 일반 모델과 다른 응답 구조를 사용:

```json
// 일반 모델 (lfm2:2.6b)
{"delta": {"content": "응답 텍스트"}}

// 추론 모델 (qwen3-tk:4b)
{"delta": {"reasoning_content": "추론 과정"}}  // 추론 중
{"delta": {"content": "최종 답변"}}            // 추론 완료 후
```

### 2. stream_processor.py의 문제

기존 코드는 `delta.content`만 처리하고 `delta.reasoning_content`를 무시:

```python
# 기존 코드 (문제)
content = delta.get("content") or ""
# reasoning_content는 처리되지 않음!
```

### 3. 추가 누락 사항

- `flm-llm-thinking` 프로바이더가 그룹에 누락
- `provider_name_map`에 매핑 누락
- `PROVIDER_DEVICE_GROUP`에 NPU 그룹 누락

## 해결 방법

### 1. stream_processor.py 수정

`reasoning_content`를 `<think>` 태그로 감싸서 전송:

```python
# 수정된 코드
delta = choices[0].get("delta") or {}
if not isinstance(delta, dict):
    delta = {}

reasoning_content = str(delta.get("reasoning_content") or "")
content = str(delta.get("content") or "")

# 추론 과정이 있으면 <think> 태그로 감싸서 전송
if reasoning_content:
    if full_content is None:
        full_content = ""
    if not full_content.startswith("<think>"):
        full_content = "<think>"
        await self.publish_chunk(request_id, "<think>")
    full_content += reasoning_content
    await self.publish_chunk(request_id, reasoning_content)

# 최종 답변이 있으면 </think> 닫고 전송
if content:
    if full_content is None:
        full_content = ""
    if full_content.startswith("<think>") and "</think>" not in full_content:
        full_content += "</think>\n\n"
        await self.publish_chunk(request_id, "</think>\n\n")
    # ... content 처리
```

### 2. manager.py 수정

`flm-llm-thinking`을 flm 그룹에 추가:

```python
ProviderGroup(
    name="flm",
    providers=[configs["flm-asr"], configs["flm-llm"], configs["flm-llm-thinking"], configs["flm-ocr"]],
    order=1
),
```

### 3. stream_processor.py 매핑 추가

```python
provider_name_map = {
    # ...
    "flm-llm-thinking": "flm-llm-thinking",
}

PROVIDER_DEVICE_GROUP = {
    # ...
    "flm-llm-thinking": "npu",
}
```

## 트러블슈팅 과정에서 배운 점

### 1. PM2 코드 리로드 문제

PM2 `restart`만으로는 Python 코드가 리로드되지 않을 수 있음:

```bash
# 안전한 재시작 방법
pm2 stop provider-manager
# 포트 점유 프로세스 강제 종료
powershell -Command "Stop-Process -Id $(netstat -ano | grep 9998 | awk '{print $5}') -Force"
pm2 start ecosystem.config.js
```

### 2. 단계별 디버깅

스트리밍 체인의 각 단계를 개별 테스트:

```bash
# 1. FLM 서버 직접 테스트
curl -sN http://localhost:11437/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-tk:4b","messages":[{"role":"user","content":"1+1"}],"stream":true}'

# 2. Backend API 테스트
curl -sN "http://localhost:8000/api/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"1+1","reasoning_mode":true}'
```

### 3. 방어적 코딩의 중요성

None 체크와 타입 검증이 중요:

```python
# 안전한 방식
delta = choices[0].get("delta") or {}
if not isinstance(delta, dict):
    delta = {}
reasoning_content = str(delta.get("reasoning_content") or "")
```

## 수정된 파일

| 파일 | 변경 내용 |
|------|-----------|
| `infra/provider_manager/services/stream_processor.py` | `reasoning_content` 처리, `<think>` 태그 래핑, 매핑 추가 |
| `infra/provider_manager/core/manager.py` | `flm-llm-thinking` 그룹 추가 |
| `infra/provider_manager/core/config.py` | `flm_llm_thinking_url` 설정 추가 |

## 관련 컴포넌트

- **Frontend**: `ThinkingProcessAccordion.tsx` - `<think>` 태그 파싱 및 아코디언 표시
- **ChatInterface.tsx**: 스트리밍 토큰에서 `<think>` 태그 추출 로직
