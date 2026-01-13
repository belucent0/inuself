# Architecture V5: API-based ASR & Diarization Orchestration

**핵심 철학**: LiteLLM은 "관문(Routing)", Worker는 "조율(Orchestration)", Provider는 "수행(Execution)"

## 1. 시스템 구성도

```mermaid
flowchart TD
    User([User]) --> Backend[Backend API]

    %% 1. Realtime Path (Chat / Stream)
    Backend -->|Chat / Stream| LiteLLM

    %% 2. Batch Path (File Upload)
    Backend -->|Job: File Processing| Redis[(Redis Queue)]
    Redis --> Worker[Unified Worker]

    %% Worker Orchestration Logic
    subgraph WorkerProcessing [Worker Orchestration]
        Pre[Pre-processing: Conv to WAV]
        
        subgraph ParallelCalls [Parallel API Calls]
            CallASR[Call ASR API]
            CallDiar[Call Diarization API]
        end
        
        Post[Post-processing: Merge Results]
    end
    
    Worker --> Pre --> ParallelCalls
    CallASR --> Post
    CallDiar --> Post

    %% 3. API Gateway (LiteLLM)
    subgraph Gateway [LiteLLM Router]
        LiteLLM[LiteLLM Proxy]
    end

    %% API Calls from Worker
    CallASR -->|POST /v1/audio/transcriptions| LiteLLM
    CallDiar -->|POST /v1/audio/diarization| LiteLLM

    %% 4. AI Providers
    subgraph Providers [AI Providers]
        direction TB
        NPU[FLM Server (NPU ASR)]
        GPU_ASR[Whisper Server (GPU ASR)]
        GPU_Diar[Diarization Server (GPU)]
    end

    %% Routing Logic
    LiteLLM -->|Route: Speed Mode| NPU
    LiteLLM -->|Route: Accuracy Mode| GPU_ASR
    LiteLLM -->|Route: Diarization| GPU_Diar

    %% 5. Final Save
    Post -->|Save Merged Result| DB[(Database)]
    Post -->|Save Merged Result| S3[(Storage)]
```

---

## 2. 구성 요소별 역할

### Unified Worker (Orchestrator)
- **역할**: 작업의 시작과 끝을 책임지는 지휘자
- **주요 기능**:
  - **전처리**: 다양한 오디오 포맷을 WAV(16kHz)로 변환
  - **병렬 호출**: `ThreadPoolExecutor`를 사용하여 ASR과 Diarization API 동시 호출
  - **후처리 (Merging)**: ASR의 텍스트 세그먼트와 Diarization의 화자 타임라인을 매핑하여 최종 결과 생성
  - **직접 추론 금지**: 무거운 AI 모델은 로드하지 않음 (API 호출만 수행)

### LiteLLM (Router)
- **역할**: 모든 AI 요청의 단일 진입점 (Single Entry Point)
- **주요 기능**:
  - **ASR 라우팅**: 요청된 모드(Speed/Accuracy)와 자원 상태(NPU/GPU 부하)를 기반으로 적절한 Provider 선택
  - **Diarization 라우팅**: 화자분리 요청을 전담 GPU 서버로 전달
  - **Provider 관리**: 필요 시 Provider 시작 신호 전송 (Auto-start)

### AI Providers (Executors)
- **FLM Server (NPU)**: 빠른 속도의 ASR 처리 (Speed Mode)
- **Whisper Server (GPU)**: 높은 정확도의 ASR 처리 (Accuracy Mode), whisper.cpp 기반 HTTP 서버
- **Diarization Server (GPU)**: PyAnnote 기반 화자분리 전용 서버

---

## 3. 요청 처리 시나리오 (배치 ASR)

1. **사용자 업로드**: 파일 업로드 → Backend → Redis Queue에 작업 등록
2. **작업 시작**: Worker가 작업 수령
3. **전처리**: ffmpeg로 오디오 변환
4. **API 호출 (병렬)**:
   - **ASR 요청**: `POST https://litellm/v1/audio/transcriptions`
     - Body: `{ "model": "whisper-turbo", "accuracy_mode": "speed" }`
     - LiteLLM: NPU 여유 시 FLM으로, 아니면 GPU로 라우팅
   - **Diarization 요청**: `POST https://litellm/v1/audio/diarization`
     - Body: `{ "model": "pyannote" }`
     - LiteLLM: GPU Diarization Server로 전달
5. **결과 병합**: 두 API 응답(JSON)을 받아 타임스탬프 기준으로 병합
6. **저장**: 최종 결과를 DB에 저장하고 완료 알림 전송

---

## 4. V4와의 차이점

| Feature | Architecture V4 | Architecture V5 (Proposed) |
|---------|----------------|---------------------------|
| **Audio Gateway** | 존재함 (ASR+Diar 통합 수행) | **제거됨** (기능별 서버로 분해) |
| **Worker 역할** | API 호출 (단일 파이프라인) | **Orchestrator** (병렬 호출 + 병합) |
| **병합 위치** | Audio Gateway 내부 | **Worker 내부** |
| **LiteLLM 역할** | 라우팅 + (일부 병합 논의됨) | **순수 라우팅** |
