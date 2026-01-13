# 시스템 전체 조감도 (System Architecture Overview)

현재 시스템의 모든 컴포넌트와 데이터/요청 흐름을 보여주는 조감도입니다.

```mermaid
flowchart TD
    %% 사용자 및 프론트엔드
    User([User]) -->|Browser| Frontend[Frontend Next.js]
    Frontend -->|HTTP/WS| Nginx[Nginx Proxy]

    %% 백엔드 및 코어 서비스
    subgraph Core_Services [Core Services]
        Nginx -->|/api| Backend[Backend API FastAPI]
        Nginx -->|/socket.io| Backend
        Backend -->|Chat/Summary Request| LiteLLM[LiteLLM Proxy]
    end

    %% 데이터 인프라
    subgraph Data_Layer [Data Infrastructure]
        Backend -->|Persist| DB[(PostgreSQL)]
        Backend -->|File Storage| S3[(MinIO S3)]
        Backend -->|Task Queue/Cache| Redis[(Redis)]
        LiteLLM -.->|Check Semaphore| Redis
    end

    %% 워커 (비동기 처리)
    subgraph Worker_Layer [Worker Layer - Celery]
        Redis -->|Consume Task| W_ASR[Worker ASR]
        Redis -->|Consume Task| W_LLM[Worker LLM]
        Redis -->|Consume Task| W_OCR[Worker OCR]
    end

    %% 컴퓨팅/모델 리소스
    subgraph Compute_Resources [AI Compute Resources]
        direction TB
        
        %% NPU 섹션
        NPU_APP[NPU App FLM Server]
        
        %% GPU 섹션 (PM2)
        GPU_Default[GPU Server Llama-4B Chat]
        
        %% On-Demand 섹션
        GPU_OnDemand[GPU On-Demand Vision 8B OCR]
    end

    %% 서비스 연동 관계
    LiteLLM -->|Normal Chat| NPU_APP
    LiteLLM -->|Heavy Load / Fallback| GPU_Default
    
    W_ASR -->|Whisper| GPU_Default
    W_LLM -->|Summary| LiteLLM
    
    %% OCR 워커의 동적 처리
    W_OCR -->|Speed Mode| NPU_APP
    W_OCR -.->|Accuracy Mode Launch & Request| GPU_OnDemand
    
    %% 세마포어 로직
    W_OCR -- Set: worker:npu --> Redis
    W_OCR -- Set: worker:gpu --> Redis

    %% 스타일링
    classDef default fill:#f9f9f9,stroke:#333,stroke-width:1px;
    classDef core fill:#e1f5fe,stroke:#01579b,stroke-width:2px;
    classDef data fill:#fff3e0,stroke:#e65100,stroke-width:2px;
    classDef worker fill:#e8f5e9,stroke:#1b5e20,stroke-width:2px;
    classDef compute fill:#f3e5f5,stroke:#4a148c,stroke-width:2px,color:#000;
    
    class Backend,LiteLLM core;
    class DB,S3,Redis data;
    class W_ASR,W_LLM,W_OCR worker;
    class NPU_APP,GPU_Default,GPU_OnDemand compute;
```

---

## 컴포넌트 설명

### 1. **Core Services**
- **Nginx**: 모든 트래픽의 진입점.
- **Backend (FastAPI)**: 비즈니스 로직, DB/S3 연동, 작업 큐(Redis) 적재.
- **LiteLLM**: LLM 요청의 "교통경찰". 세마포어(GPU/NPU 사용 여부)를 확인하여 라우팅.

### 2. **Worker Layer (Celery)**
- **OCR Worker**:
  - **Speed 모드**: NPU(FLM) 사용, `worker:npu` 세마포어 점유.
  - **Accuracy 모드**: GPU 전용 8B Vision 모델을 **일시적으로(On-Demand) 실행**하고 종료. `worker:gpu` 세마포어 점유.
- **ASR/LLM Worker**: 오디오 및 요약 작업 담당.

### 3. **AI Compute Resources**
- **NPU (FLM)**: 상시 실행 (채팅/빠른OCR).
- **GPU (Default)**: 상시 실행 (채팅 백업/Llama-4B).
- **GPU (On-Demand)**: 필요 시에만 실행 (정밀OCR/Vision 8B/Port 8082).
