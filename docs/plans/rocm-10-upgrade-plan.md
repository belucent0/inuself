# ROCm 10.0.0 WSL 업그레이드·성능 비교 계획

- 작성일: 2026-08-29
- 상태: 계획 승인 전 — 운영 이미지는 ROCm 7.2.1 계열 유지

## 결론

ROCm Core SDK 10.0.0은 2026-08-26 정식 공개되었고 `gfx1150` 및 WSL용
ROCDXG 설치 경로를 제공한다. AMD는 ROCm 10.0.0 + PyTorch 2.12 + vLLM
0.27 조합의 컨테이너도 문서화했다.

다만 이 호스트는 WSL2 `/dev/dxg`, 31.8 GiB UMA, Gemma 4 A4B MTP 패치,
ROCr idle-poll 수정에 의존한다. 따라서 호스트와 운영 이미지를 바로 교체하지
않고, ROCm 10 후보 이미지를 별도 태그로 설치한 뒤 동일 입력의 설치 전·후
성능을 비교한다. 첫 승격 대상은 `ai-llm` 하나이며 ASR과 pyannote는 그 다음
단계에서 각각 추가한다.

공식 근거:

- [ROCm 10.0.0 release notes](https://rocm.docs.amd.com/en/docs-10.0.0/about/release-notes.html)
- [ROCm 10.0.0 installation and WSL ROCDXG](https://rocm.docs.amd.com/en/docs-10.0.0/install/rocm.html)
- [AMD vLLM on ROCm](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/inference/vllm.html)
- [vLLM ROCm installation](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/)
- [TheRock transition guide](https://rocm.docs.amd.com/en/docs-10.0.0/about/transition-guide-TheRock.html)
- [ROCr WSL idle-poll fix](https://github.com/ROCm/rocm-systems/pull/7898)
- [ROCm 10 source tag (`therock-10.0`)](https://github.com/ROCm/rocm-systems/tree/therock-10.0)

## 현재 기준선

운영 설정은 변경하지 않는다.

| 항목 | 현재 값 |
|---|---|
| GPU | Radeon 890M, `gfx1150`, WSL2 `/dev/dxg` |
| WSL | Ubuntu 24.04.3, kernel `6.6.87.2-microsoft-standard-WSL2` |
| WSL 한도 | 32 GiB, iGPU hard stop 30.8 GiB |
| LLM runtime | PyTorch `2.11.0+gitd0c8b1f`, HIP `7.2.53211`, vLLM `0.26.0` |
| LLM | Gemma 4 26B A4B AWQ INT4 |
| LLM profile | 32K / seq4 / KV 3 GiB / batch1024 / MTP k4 |
| ASR | Whisper large-v3-turbo vLLM, KV 512 MiB / seq1 |
| diarization | pyannote community-1 |
| embedding | CPU EmbeddingGemma |
| rollback image | `*-rocr-pollfix-46558b7` Compose override |

과거 수치는 회귀 감지용 참고값이다. 직접 비교는 전환 당일 현재 이미지와
후보 이미지를 같은 전원 모드·입력·요청 순서로 연속 측정한다.

| 과거 측정 | 기준값 |
|---|---:|
| A4B 단독, 128-token 3회 평균 | 23.410 tok/s |
| 전체 추론 스택 상주, 128-token 3회 평균 | 5.629 tok/s |
| short 4-way, seq4/batch1024 | 10.446 tok/s |
| 4 x 16K aggregate | 1.085 tok/s |
| Whisper vLLM 단건 | RTF 0.375 |
| warm pyannote 33.54초 샘플 | 5.63초, RTF 0.168 |
| 전체 스택 정상 상주 | 27.896 GiB, swap 0 |
| ASR+pyannote 경합 peak | 30.191 GiB |

세부 원본은
[`gemma4-a4b-vllm-tuning-draft.md`](../benchmarks/gemma4-a4b-vllm-tuning-draft.md)와
[`asr-vllm-vs-whispercpp.md`](../benchmarks/asr-vllm-vs-whispercpp.md)에 있다.

## 확인해야 할 호환성 차이

| 변화 | 확인 사항 |
|---|---|
| ROCm 7.2.1 → 10.0.0 | TheRock 전환으로 설치 경로가 `/opt/rocm/core-10.0`, 패키지 이름이 `amdrocm-*`로 변경됨 |
| vLLM 0.26 → 0.27 | A4B AWQ, Gemma 4 vision, MTP k4, Whisper endpoint가 모두 실제로 기동되는지 확인 |
| Python 3.12 → AMD 이미지의 3.14 | `sitecustomize.py`, MTP backport, fake `amdsmi`의 고정 경로를 그대로 복사하지 않음 |
| PyTorch 2.11 계열 → 2.12 | pyannote/torchaudio/torchcodec ABI를 LLM과 분리해 검증 |
| ROCDXG 1.1.2 → 1.2.2 | `/dev/dxg`, `libdxcore.so`, `librocdxg.so`, `dids.conf` 마운트 계약 확인 |
| ROCr runtime | `therock-10.0` 태그에 `46558b7` backoff 코드가 없음; ROCm 10 실험 이미지에도 patched ROCr 유지 |

ROCr 수정은 2026-08-18 `develop`에 병합되어 ROCm 10 출시보다 빠르지만,
공식 `therock-10.0` 소스 태그의 `runtime.cpp`에는 `poll_nap_us`가 없고
`poll_backoff.h`도 존재하지 않는다. 따라서 stock ROCm 10은 비교군으로만 쓰고,
운영 후보에는 `46558b7`을 적용한 ROCr를 유지한다. 향후 공식 태그에 수정이
포함된 뒤 소스와 실제 idle CPU를 모두 통과해야 커스텀 런타임을 제거한다.

## 단계별 진행

### 0. 설치 전 스냅샷

다음 정보를 결과 문서에 저장한다.

```bash
uname -r
cat /etc/os-release
test -e /dev/dxg
docker image inspect ai-llm-gemma4:0.26.0-rocr-pollfix-46558b7 --format '{{.Id}}'
docker exec ai-llm python -c "import torch,vllm; print(torch.__version__, torch.version.hip, vllm.__version__)"
docker stats --no-stream ai-llm ai-asr-vllm ai-diarize
```

Windows AMD 드라이버 버전과 WSL 버전도 함께 기록한다. 호환 드라이버가
확정되기 전에는 Windows 드라이버나 WSL 배포판을 바꾸지 않는다.

### 1. 현재 이미지의 당일 baseline

콜드 ROCm/Triton JIT 1회는 버리고 각 항목을 최소 3회 실행해 median을 쓴다.

```bash
python infra/inference/bench/concurrent_bench.py \
  --base-url http://localhost:18000 \
  --model gemma4-a4b \
  --concurrency 1,4 \
  --requests-per-level 8 \
  --max-tokens 128

python infra/inference/bench/ocr_smoke.py /path/to/same-image.png
```

함께 기록할 값은 TTFT, aggregate output TPS, 요청별 TPS, prompt/output token,
MTP acceptance, wall time, iGPU peak, WSL available memory, swap 증가량이다.

### 2. ROCm 10 후보 이미지 설치

AMD가 문서화한 최초 PoC 기준은 다음 이미지다.

```bash
docker pull rocm/vllm:rocm10.0.0_ubuntu24.04_py3.14_pytorch_2.12.0_vllm_0.27.0
```

운영 태그를 덮어쓰지 않고 `ai-llm-gemma4:0.27.0-rocm10.0.0-exp` 같은 별도
태그와 별도 Compose override를 만든다. 모델 캐시는 재사용하되 운영 컨테이너와
동시에 GPU에 올리지 않는다.

upstream vLLM은 `vllm/vllm-openai-rocm` 이미지를 권장하고 AMD의
`rocm/vllm*` 이미지를 deprecated로 표시한다. 반면 AMD 문서는 위 ROCm 10
이미지를 검증 조합으로 제시한다. Phase 0에서 두 이미지의 내부 ROCm/PyTorch/
vLLM/gfx1150 지원을 확인하고, ROCm 10이 명시된 digest를 실험 대상으로 고정한다.

현재의 Python 3.12 경로 기반 MTP backport와 profiler/amdsmi stub은 후보에
무조건 적용하지 않는다. upstream 포함 여부를 먼저 확인하고, 실제 import 또는
WSL 초기화 실패를 재현한 경우에만 필요한 최소 shim을 추가한다.

stock ROCm 10과 `46558b7` 적용 ROCm 10을 짧게 비교해 기능·속도 차이가 없는지
확인하되, stock의 idle CPU가 다시 상승하면 즉시 중단하고 patched 후보만 다음
단계로 보낸다.

### 3. A4B 단독 설치 후 속도 비교

ASR과 pyannote를 내린 상태에서 현재와 동일한 모델 및 32K/seq4/KV3GiB/
batch1024/MTP k4로 서버를 띄운다. 다음 순서로 비교한다.

1. `/v1/models`, 짧은 text completion, 한국어 completion
2. MTP k4 로드 및 draft acceptance
3. 단건 128-token 3회 median
4. concurrency 1/2/4 aggregate TPS와 TTFT
5. 4 x 8K, 4 x 16K long-context
6. 동일 이미지 OCR
7. 요청 없는 상태 60초 CPU와 메모리

속도 결과는 다음 표에 채운다.

| 항목 | ROCm 7.2.1/vLLM 0.26 | ROCm 10/vLLM 0.27 | 변화율 |
|---|---:|---:|---:|
| 단건 128-token TPS median | TBD | TBD | TBD |
| concurrency 4 aggregate TPS | TBD | TBD | TBD |
| concurrency 4 TTFT p50 | TBD | TBD | TBD |
| 4 x 16K aggregate TPS | TBD | TBD | TBD |
| OCR wall time / 정확성 | TBD | TBD | TBD |
| ai-llm idle CPU 60초 평균 | TBD | TBD | TBD |
| iGPU resident / peak | TBD | TBD | TBD |

### 4. 전체 스택 비교

A4B 단독이 통과한 뒤에만 Whisper vLLM을 ROCm 10 후보로 추가한다. pyannote는
마지막에 별도 이미지로 추가한다. 각 추가 단계에서 real request를 실행하고 peak
메모리를 확인한다.

```bash
python infra/inference/bench/concurrent_bench.py \
  --base-url http://localhost:18000 \
  --model gemma4-a4b \
  --concurrency 1,4 \
  --requests-per-level 8 \
  --max-tokens 128

docker exec ai-diarize python /bench/pyannote_smoke.py /audio/test_10s.wav
```

Whisper는 운영 `/v1/audio/transcriptions` 경로로 동일 파일을 3회 처리해 RTF와
전사 결과를 비교한다. 동시에 A4B 4-way, Whisper 1건, pyannote 1건을 겹쳐
경합 TPS/RTF와 30.8 GiB hard stop을 확인한다. 마지막으로 Gateway 실제 chat,
OCR, ASR, diarization, embedding과 Langfuse Eval Gate를 실행한다.

### 5. 승격과 soak

다음 조건을 모두 만족할 때만 ROCm 10 이미지를 운영 override로 승격한다.

- A4B 단건 및 concurrency median TPS가 당일 baseline보다 5% 넘게 느리지 않음
- TTFT, Whisper RTF, pyannote wall time가 10% 넘게 악화되지 않음
- MTP k4가 정상 동작하고 acceptance가 기존 workload에서 유의하게 하락하지 않음
- text/OCR/ASR/diarization/embedding 및 Langfuse eval 전부 성공
- iGPU peak 30.8 GiB 이하, WSL available memory 4 GiB 이상, swap 증가 없음
- 각 GPU 서비스 idle CPU가 현재 poll-fix 수준을 유지
- 1시간 혼합 부하와 시스템 재부팅 후 재기동에서 crash/restart/DXG 오류 없음

성능이 동률이어도 ROCm 10의 gfx1150/WSL 및 vLLM 0.27 유지보수 이점은 승격
근거가 될 수 있다. 단, 커스텀 ROCr는 향후 공식 패치 릴리스가 `46558b7`을
포함할 때까지 유지한다. 기능만 동작하고 TPS/RTF가 기준을 넘게 퇴보하면 ROCm
7.2.1 운영을 유지한다.

## 롤백

운영 rollback은 호스트 패키지 제거가 아니라 Compose 이미지 전환으로 끝나야 한다.

```powershell
docker compose -f docker-compose.yml -f docker-compose.rocr-wsl-pollfix.yml `
  up -d --no-build ai-llm ai-asr-vllm ai-diarize
```

ROCm 10 검증 완료 전에는 다음을 삭제하거나 덮어쓰지 않는다.

- `ai-llm-gemma4:0.26.0-rocr-pollfix-46558b7`
- `ai-diarize:1.0.0-rocr-pollfix-46558b7`
- `docker-compose.rocr-wsl-pollfix.yml`
- 현재 A4B/Whisper/pyannote 모델 캐시
