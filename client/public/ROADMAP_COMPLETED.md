# ✅ 완료된 항목

## 🏗️ 아키텍처 리팩토링 (2026-01)

대규모 구조 개선 작업으로 Backend-Worker 완전 분리 아키텍처를 구현했습니다.

### 리팩토링 배경

**이전 문제점:**
- 워커가 백엔드 코드(DB 모델, 설정 등)에 강하게 의존
- 워커에서 직접 DB 접근으로 인한 결합도 증가
- OCR 처리 시 워커에서 PDF→이미지 변환(CPU 작업)까지 수행
- 배포 및 확장 시 의존성 관리 어려움

**해결 방향:**
- 워커는 순수 GPU 작업만 수행 (DB 접근 완전 제거)
- Redis Stream을 통한 느슨한 결합 (Kafka 경량 대안)
- CPU 작업(전처리)은 백엔드에서, GPU 작업만 워커에서 수행

### 변경된 아키텍처

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  FastAPI API    │────▶│  Redis/Celery   │────▶│  GPU Workers    │
│  (backend/)     │     │  (Task Queue)   │     │  (worker/)      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │                       │
        │                       ▼                       │
        │              ┌─────────────────┐              │
        │              │  Redis Stream   │◀─────────────┘
        │              │  (Result Queue) │   결과 발행
        │              └─────────────────┘
        │                       │
        ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│   PostgreSQL    │◀────│ StreamConsumer  │
│   (Results DB)  │     │  (Backend)      │
└─────────────────┘     └─────────────────┘
```

### 주요 변경 파일

**워커 (worker/):**
- `worker/utils/storage.py` - S3 클라이언트 (백엔드 독립)
- `worker/utils/result_publisher.py` - Redis Stream 발행
- `worker/utils/postprocess.py` - ASR 후처리 함수
- `worker/processors/asr_processor.py` - ASR 처리 (DB 접근 제거)
- `worker/processors/llm_processor.py` - LLM 처리 (text_to_summarize 입력)
- `worker/processors/ocr_processor.py` - OCR 처리 (image_s3_keys 입력)

**백엔드 (backend/app/):**
- `services/stream_consumer.py` - Redis Stream 구독 → DB 저장 (신규)
- `services/ocr_service.py` - OcrPreprocessor (PDF→이미지 전처리만)
- `utils/task_queue_adapter.py` - 워커 큐잉 인터페이스 변경
- `core/storage.py` - download_json, delete_files_by_prefix 추가

### 워커 입출력 인터페이스 변경

| 워커 | 이전 입력 | 변경 후 입력 | 출력 |
|------|----------|-------------|------|
| ASR | file_id (DB 조회) | file_id, s3_key | S3 + Redis Stream |
| LLM | file_id (DB 조회) | file_id, text_to_summarize | Redis Stream |
| OCR | file_id (DB 조회) | file_id, image_s3_keys | Redis Stream |

---

## 🎤 실시간 ASR 스트리밍 (2026-01)

WebSocket 기반 실시간 오디오 스트림 전사 기능 구현

### 처리 흐름

```
Client (Next.js)          Backend (FastAPI)           FLM Server (NPU)
      │                          │                          │
      │ 1. WebSocket 연결         │                          │
      │─────────────────────────▶│                          │
      │                          │ 2. FLM 서버 확인          │
      │                          │─────────────────────────▶│
      │◀─────────────────────────│ 3. "ready" 메시지         │
      │                          │                          │
      │ 4. 5초 오디오 (바이너리)   │                          │
      │─────────────────────────▶│ 5. WebM→WAV 변환         │
      │ 4. "audio_chunk" JSON    │─────────────────────────▶│
      │─────────────────────────▶│ 6. Whisper 전사 (NPU)    │
      │                          │◀─────────────────────────│
      │◀─────────────────────────│ 7. "commit" 메시지        │
      │                          │                          │
      │                          │ 8. LLM 후처리 (백그라운드) │
      │                          │─────────────────────────▶│
      │◀─────────────────────────│ 9. "correction" 메시지    │
```

### 주요 구현 사항

**클라이언트 (`StreamingASRModal.tsx`):**
- MediaRecorder 5초마다 stop/start로 독립 WebM 청크 생성
- VAD (Voice Activity Detection) 필터링 - 침묵 구간 전송 생략
- 바이너리 오디오 + JSON 제어 메시지 분리 전송

**백엔드 (`websocket_controller.py`):**
- `asyncio` 기반 비동기 처리 (이벤트 루프 충돌 해결)
- `run_in_executor`로 ffmpeg 블로킹 호출 처리
- FLM `/v1/audio/transcriptions` 엔드포인트 호출

**LLM 후처리 (`websocket_helper.py`):**
- 언어 필터링: 한국어/영어 외 → "음성 인식 불가"
- 문법 교정, 구두점 추가
- JSON 형식 응답 파싱 (fallback 로직 포함)

### 해결한 기술적 이슈

1. **`asyncio.run()` 이벤트 루프 충돌** → async 함수로 변환
2. **WebM 청크 헤더 문제** → MediaRecorder stop/start로 독립 파일 생성
3. **FLM 엔드포인트 경로** → `/chat/completions` → `/v1/chat/completions`
4. **메시지 타입 불일치** → `result` → `commit` 타입으로 통일

---

## 🚀 NPU 가속 통합 (2026-01)

AMD Ryzen AI NPU를 활용한 ASR 및 LLM 처리 가속

### ASR 모드 비교

| 모드 | Provider | 가속 | 용도 |
|------|----------|------|------|
| 실시간 스트리밍 | FLM (Whisper-V3-Turbo) | NPU | WebSocket 실시간 전사 |
| 정확도 모드 | whisper.cpp(Whisper-V3) | Vulkan GPU | 파일 업로드 배치 처리 |

### FLM 서버 설정

- PM2로 `flm-server` 프로세스 관리
- `ecosystem.config.js`에 FLM 서버 설정 포함
- 환경변수: `FLM_BASE_URL`, `FLM_LLM_MODEL`

---

## 🔧 기타 완료 항목

### Redis Stream 기반 결과 전달

Kafka의 경량 대안으로 Redis Stream 도입

**구현 내용:**
- `worker/utils/result_publisher.py` - 결과 발행
- `backend/app/services/stream_consumer.py` - 결과 구독 및 DB 저장
- 스트림 키: `worker:results:{task_type}`

### OCR 전처리 분리

**이전:** 워커가 PDF→이미지 변환 + OCR 전체 처리
**이후:** 백엔드(CPU)에서 전처리, 워커는 GPU/NPU OCR만

**흐름:**
1. 백엔드: PDF → 이미지 변환 (pdf2image/poppler)
2. 백엔드: 이미지 S3 임시 저장
3. 백엔드: `enqueue_ocr_job(file_id, image_s3_keys)` 큐잉
4. 워커: 이미지 다운로드 → LLM Vision OCR
5. 워커: Redis Stream으로 결과 발행
6. 백엔드 StreamConsumer: DB 저장 + 임시 이미지 삭제

### 워커 Processor 모듈화

DB 접근 코드를 제거하고 순수 처리 로직만 포함

**파일 구조:**
```
worker/processors/
├── asr_processor.py   # process_asr(file_id, s3_key, ...)
├── llm_processor.py   # process_llm(file_id, text_to_summarize, ...)
└── ocr_processor.py   # process_ocr(file_id, image_s3_keys, ...)
