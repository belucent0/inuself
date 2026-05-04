# 아키텍처 설계 근거: 선택의 이유와 전제 조건

> **대상 문서**: `03-architecture-evolution.md` 설계 결정에 대한 보충 기록
> **성격**: "왜 이렇게 만들었나" — 서사가 아닌 근거 중심

---

## 1. SOLID 원칙 관점의 설계 평가

### 명확히 개선된 항목

**OCP (개방-폐쇄 원칙) — 가장 큰 개선**

Legacy에서 NPU를 추가하면 Worker 3곳의 코드를 수정해야 했다. 현재 구조에서는 LiteLLM config 1줄로 끝난다. "확장에 열려있고, 기존 코드는 닫혀있다"는 원칙이 실제 측정 가능한 결과로 나타난다.

**DIP (의존성 역전 원칙)**

```
Legacy:  Worker (고수준) → Llama-Server URL (저수준 구체)
현재:    Worker (고수준) → LiteLLM endpoint (추상)
                              ↓
                   Provider Manager → GPU/NPU (구체)
```

고수준 모듈이 추상에만 의존하고, 하드웨어 구체 구현은 Provider Manager가 격리한다.

**SRP (단일 책임 원칙)**

각 컴포넌트의 변경 이유가 분리됐다. 모델 추가는 LiteLLM config만, 전처리 로직 변경은 Worker만, 하드웨어 lifecycle은 Provider Manager만 건드리면 된다.

### 논의가 필요한 항목

**Provider Manager의 SRP 경계**

StreamProcessor, ProviderManager, IdleManager, Semaphore를 하나의 Host 프로세스가 담당한다. 현재 규모에서는 합리적이나, 각 기능이 커지면 분리 검토 필요.

**Valkey Stream과 YAGNI**

단일 머신 IPC에 Valkey Stream은 다소 무거운 선택이다. 그러나 추론 서버를 전용 하드웨어로 분리할 때 컨테이너 코드를 건드리지 않아도 된다는 점에서 그 무게를 감수했다. "필요할 때 추가한다"는 YAGNI와 긴장 관계에 있지만, 이전 비용을 선불로 낸 의식적 선택이다.

---

## 2. 운영 환경 제약: AMD ROCm + WSL2 미지원

### 제약 사실

AMD Ryzen APU의 ROCm은 현재 WSL2를 공식 지원하지 않는다. NVIDIA CUDA가 WSL2 GPU 패스스루를 공식 지원하는 것과 대조적이다.

```
NVIDIA + WSL2           →  CUDA 정상 동작  ✅
AMD Ryzen APU + WSL2    →  ROCm 미지원    ❌
```

### 이 제약이 아키텍처에 미치는 영향

- Provider Manager는 반드시 Windows Host에서 실행해야 한다
- GPU/NPU 모델 서버도 Windows Native 필수
- Docker Desktop 불안정 문제는 아키텍처가 아닌 **OS 레벨 제약**이다
  - WSL2 + Docker Engine 직접 설치로 우회하는 방법이 있으나
  - AMD ROCm이 WSL2를 지원하지 않으므로 실질적 이득이 없다
- Docker Desktop 크래시는 현재 환경에서 근본 해결이 불가하다

---

## 3. PM2 선택 근거

Python 서비스를 Node.js 프로세스 매니저로 관리하는 것은 이질적으로 보일 수 있다. 선택 이유는 **cross-platform** 특성이다.

```
지금 (Windows)               Ubuntu 이전 후
─────────────────────        ─────────────────────
pm2 start                    pm2 start
ecosystem.config.js    →     ecosystem.config.js (재사용)
```

Ubuntu 이전 시 프로세스 관리 설정을 다시 작성할 필요가 없다. Ubuntu 네이티브 환경에서는 systemd로 교체하는 것이 더 자연스럽지만, 이전 과도기에는 PM2를 그대로 유지할 수 있다.

| 도구 | 장점 | 단점 |
|------|------|------|
| PM2 (현재) | cross-platform, 로그/모니터링 내장 | Node.js 도구로 Python 관리 |
| systemd | Linux 표준, OS 레벨 안정성 | Ubuntu 이전 후에만 사용 가능 |
| NSSM | Windows Service 통합 | Windows 전용 → 이전 시 버려야 함 |

---

## 4. 현재 아키텍처의 전제 조건

### 이 아키텍처를 타당하게 만드는 전제

> **"NPU Linux 지원이 성숙해지면 Ubuntu로 이전한다"**

이 전제 하에 현재 복잡도는 미래에 대한 선투자다.

```
지금 부담하는 복잡도          Ubuntu 이전 시 회수
───────────────────────       ──────────────────────────
Docker Desktop 불안정   →     Ubuntu에서 사라짐
Valkey Stream 복잡도    →     컨테이너 코드 무변경으로 이전
Provider Manager 분리   →     Host 측만 systemd로 교체
LiteLLM 추상화          →     NPU 추가가 config 1줄
```

### 전제가 흔들릴 경우

Ubuntu 이전 계획이 사라진다면 이 아키텍처의 복잡도는 정당화되기 어렵다. 전부 PM2로 돌리는 단순한 구조가 더 합리적일 수 있다. 전제 조건이 바뀌면 아키텍처 재검토가 필요하다.

### 안정성 로드맵

```
현재                          중간                     최종
Windows + Docker Desktop  →  (AMD WSL2 지원 시)   →  Ubuntu 네이티브
불안정, 감수 중               WSL2 + Docker Engine     Docker Desktop 불필요
                                                        systemd + Docker Engine
```

---

## 5. GPU 하드웨어 교체 시 종속성 분석

### 레이어별 종속성 지도

```
[컨테이너 레이어]
  Backend, Unified Worker, LiteLLM
  → LITELLM_BASE_URL만 알고 있음
  → GPU가 AMD든 NVIDIA든 전혀 무관  ✅ 종속성 없음

[Provider Manager — Host]
  → 여기서만 하드웨어를 앎
  → AMD → NVIDIA 전환 시 이 안만 수정  ⚠️ 변경 필요하지만 격리됨
```

Valkey Stream이 경계를 만들었기 때문에 컨테이너는 하드웨어 종속성이 없다. DIP가 실제로 작동하는 지점이다.

### PyTorch 종속성

PyTorch는 ROCm/CUDA 모두 동일한 API를 제공한다.

```python
# AMD ROCm이든 NVIDIA CUDA든 동일하게 동작
device = torch.device("cuda")
model = model.to(device)
```

AMD ROCm이 CUDA 호환 레이어를 구현하고 있기 때문이다. NVIDIA로 전환 시 PyTorch 코드는 변경이 없고, **설치하는 빌드 버전만 바뀐다.**

```bash
# AMD (현재)
pip install torch --index-url https://download.pytorch.org/whl/rocm...

# NVIDIA 전환 시
pip install torch --index-url https://download.pytorch.org/whl/cu...
```

### faster-whisper / Pyannote 종속성

| 컴포넌트 | AMD (현재) | NVIDIA 전환 시 |
|----------|-----------|----------------|
| Pyannote | ROCm PyTorch | CUDA PyTorch — 코드 동일 |
| faster-whisper | CTranslate2 | CUDA 가속 — 코드 동일 |
| whisper.cpp | Vulkan 가속 | CUDA 가속 — 실행 인자만 변경 |

### NVIDIA 전환 시 오히려 단순해지는 부분

현재 AMD 환경에서 필요한 호환성 패치들이 전부 불필요해진다.

```
현재 AMD에서만 필요한 것들 (NVIDIA 전환 시 제거 가능)
─────────────────────────────────────────────────────
hipsparselt 제거 패치          (torch/_rocm_init.py)
caffe2_nvrtc.dll 제외 패치     (torch/__init__.py)
torchvision meta registration 패치
pyannote SemVer 패치
MIOpen FAST 모드 환경변수 설정
```

### FLM (NPU)

AMD Ryzen AI 전용이라 NVIDIA 전환 시 사라진다. 그러나 Provider Manager 안에 격리되어 있어 컨테이너 코드는 변경이 없다. LiteLLM config에서 FLM 라우팅 항목만 제거하면 된다.

---

## 6. 프로바이더 유연성: 로컬 + 외부 혼용

### 03 문서에서 다루지 않은 확장 시나리오

`03-architecture-evolution.md`의 서사는 "PM2 워커 분산 → 단일 관문"의 진화에 집중되어 있어, 프로바이더 유연성은 결과 중 하나로만 언급된다. 실제 효과는 더 넓다.

### 외부 프로바이더 추가 시나리오

예: 심리검사 리포트 생성에 OpenAI Codex(외부 API)를 사용하는 경우.

**Legacy였다면:**
```python
# Worker LLM 코드 안에 분기 로직이 생김
if task_type == "report":
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    response = client.chat.completions.create(...)
else:
    response = httpx.post("http://localhost:8080/...")
```
API 키 관리, 에러 처리, 재시도 로직이 워커 코드 안으로 들어간다.

**현재:**
```yaml
# LiteLLM config에 항목 추가만 하면 끝
model_list:
  - model_name: report-generator
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

  - model_name: local-llm
    litellm_params:
      model: llamacpp/...
```
Backend나 Worker는 `model: "report-generator"`로 호출할 뿐, 외부 API인지 로컬 GPU인지 알지 못한다. 컨테이너 코드 변경 없음.

### 로컬 + 외부 폴백 조합

LiteLLM 라우팅 설정으로 부하 분산과 폴백을 동시에 처리할 수 있다.

```yaml
router_settings:
  fallbacks:
    - {"local-llm": ["report-generator"]}  # 로컬 GPU 과부하 시 외부 API로 자동 폴백
```

Legacy에서 이 폴백 로직을 구현하려면 워커 코드에 상태 확인 + 분기가 들어가야 했다.

### 프로바이더 유형별 추가 비용 비교

| 프로바이더 추가 유형 | Legacy | 현재 |
|---------------------|--------|------|
| 로컬 GPU 모델 교체 | 워커 N곳 URL 수정 | config 1줄 |
| NPU 채널 추가 | 워커 N곳 코드 수정 | config 1줄 |
| 외부 API 추가 (OpenAI 등) | 워커 코드에 클라이언트 추가 | config 1줄 |
| 로컬 + 외부 폴백 조합 | 워커 코드에 분기 + 상태 확인 | config 라우팅 설정 |

OCP(개방-폐쇄 원칙)가 가장 실질적으로 드러나는 지점이다. 프로바이더가 로컬이든 외부든, 하드웨어든 클라우드 API든 동일한 방식으로 추가된다.

### 프라이버시 처리

외부 API를 사용할 때 PII(개인식별정보) 유출 방지는 애플리케이션 레이어에서 1차로 처리하는 것이 가장 견고하다.

```
덜 견고한 방식:   전체 데이터 생성 → 나중에 PII 제거
더 견고한 방식:   처음부터 PII가 포함되지 않는 구조로 설계
                  (예: 검사 점수 + 해석 컨텍스트만 포함)
```

게이트웨이(LiteLLM) 레벨에서 패턴 매칭 기반 PII 감지를 2차 안전망으로 추가할 수 있으나, 컨텍스트를 모르는 패턴 매칭의 한계가 있다. 1차 방어는 항상 애플리케이션 레이어다.

---

*관련 문서: `03-architecture-evolution.md`*
