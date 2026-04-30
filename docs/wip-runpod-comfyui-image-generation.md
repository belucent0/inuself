# WIP: RunPod ComfyUI 서버리스 이미지 생성 파이프라인

> 작성일: 2026-04-08
> 브랜치: `fix/search-retry-timeout` (별도 브랜치 생성 필요)
> 상태: 코드 구현 완료, RunPod 엔드포인트 배포 미완료, 커밋 전

---

## 목적

콘텐츠 파이프라인(ASR/OCR → LLM 요약)에서 생성된 제목/키워드를 기반으로 **커버 이미지를 자동 생성**하는 기능.
RunPod Serverless + ComfyUI + SDXL-Turbo 모델 사용. 포트폴리오 강화 목적 포함.

## 핵심 결정사항

- **Backend**: RunPod Serverless (DEPLOY_MODE 무관, 이미지 생성은 항상 RunPod)
- **워크플로우 엔진**: ComfyUI (Ollama와 유사한 래퍼 — 모델 교체, 워크플로우 확장 용이)
- **모델**: SDXL-Turbo (`sd_xl_turbo_1.0_fp16.safetensors`) — 4스텝, CFG 1.0, euler sampler
- **타이밍**: 요약 완료 후 자동 생성 + 프론트엔드 재생성 버튼
- **실패 처리**: non-fatal (이미지 생성 실패해도 요약 결과 유지)
- **GPU**: RTX 3090 (SDXL-Turbo는 ~4GB VRAM, 3090 24GB로 여유)

---

## 구현 완료 파일 목록

### Phase 1: AI Gateway — 이미지 생성 엔드포인트

| 파일 | 변경 |
|------|------|
| `infra/ai-gateway/config.py` | `RUNPOD_IMAGE_BASE_URL`, `RUNPOD_IMAGE_MODEL` 환경변수 추가 |
| `infra/ai-gateway/routes/images.py` | **신규** — `POST /v1/images/generations`, ComfyUI 워크플로우 빌더 + RunPod `/runsync` 호출 |
| `infra/ai-gateway/main.py` | images 라우터 등록 |
| `docker-compose.yml` | ai-gateway에 `RUNPOD_API_KEY`, `RUNPOD_IMAGE_BASE_URL`, `RUNPOD_IMAGE_MODEL` 전달 |
| `.env.example` | RunPod 이미지 생성 관련 환경변수 문서화 |

**ComfyUI 워크플로우 노드 구성** (`_build_comfyui_workflow`):
- Node 4: `CheckpointLoaderSimple` → `sd_xl_turbo_1.0_fp16.safetensors`
- Node 6: `CLIPTextEncode` (positive prompt)
- Node 7: `CLIPTextEncode` (negative: "blurry, low quality, text, watermark")
- Node 5: `EmptyLatentImage` (512x512)
- Node 3: `KSampler` (steps=4, cfg=1.0, euler, normal)
- Node 8: `VAEDecode`
- Node 9: `SaveImage`

### Phase 2: Worker — 이미지 생성 파이프라인

| 파일 | 변경 |
|------|------|
| `worker/pipelines/image/__init__.py` | **신규** (빈 파일) |
| `worker/pipelines/image/generator.py` | **신규** — AI Gateway `/v1/images/generations` 호출, b64 → bytes 변환 |
| `worker/processors/llm_processor.py` | LLM 완료 후 `_generate_cover_image()` 호출 (non-fatal try/except) |
| `worker/utils/result_publisher.py` | `publish_llm_completed`에 `image_s3_key` 파라미터 추가 |

**이미지 프롬프트 템플릿** (generator.py):
```
"A clean, modern digital illustration representing: {title}. Keywords: {keywords}. Style: professional, minimalist, soft gradient background, no text overlay."
```

### Phase 3: Backend — 이미지 저장 및 API

| 파일 | 변경 |
|------|------|
| `backend/app/db/models.py` | Content 모델에 `cover_image_key: String(1024)` 추가 |
| `backend/alembic/versions/20260408_01_add_cover_image_key_to_content.py` | **신규** — Alembic 마이그레이션 |
| `backend/app/repositories/file_repository.py` | `update_cover_image_key()` 메서드 추가 |
| `backend/app/services/stream_consumer.py` | LLM 완료 이벤트에서 `image_s3_key` 읽어 DB 저장 |
| `backend/app/services/content_service.py` | `cover_image_url` 응답 세팅 + `regenerate_cover_image()` 메서드 추가 |
| `backend/app/schemas/content.py` | `ContentBaseSchema`에 `cover_image_url: str | None` 추가 |
| `backend/app/controllers/media_controller.py` | `GET /api/media/cover/{content_id}` — S3 커버 이미지 프록시 |
| `backend/app/controllers/content_controller.py` | `POST /{content_id}/regenerate-image` — 재생성 엔드포인트 |

### Phase 4: Frontend — 이미지 표시 + 재생성

| 파일 | 변경 |
|------|------|
| `frontend/src/features/content/types/index.ts` | `ContentSummary`에 `cover_image_url?: string` 추가 |
| `frontend/src/shared/services/endpoints/contents.ts` | `regenerateCoverImage()` API 함수 추가 |
| `frontend/src/features/content/components/ContentCard.tsx` | 카드 상단에 커버 이미지 표시 (COMPLETED 상태만) |
| `frontend/src/features/content/components/SummaryDisplay.tsx` | 커버 이미지 표시 + hover 시 재생성 버튼, 이미지 없으면 "커버 이미지 생성" 버튼 |

---

## 남은 작업

### 1. RunPod 엔드포인트 배포 (필수)

1. [runpod.io](https://runpod.io) 가입 → 크레딧 충전 ($10+) → API Key 생성
2. Serverless > New Endpoint:
   - Docker Image: `runpod/worker-comfyui:3.7.1-sdxl`
   - GPU: RTX 3090
   - Min Workers: 0, Max Workers: 2
3. SDXL-Turbo 모델 추가 (Network Volume에 `sd_xl_turbo_1.0_fp16.safetensors` 다운로드)
4. `.env`에 설정:
   ```
   RUNPOD_API_KEY=rpa_XXXXXXXXXXXXXXXX
   RUNPOD_IMAGE_BASE_URL=https://api.runpod.ai/v2/<endpoint-id>
   RUNPOD_IMAGE_MODEL=sdxl-turbo
   ```

### 2. DB 마이그레이션 실행

```bash
cd backend && alembic upgrade head
```

### 3. 테스트

- AI Gateway 단위: `curl -X POST http://localhost:4000/v1/images/generations -d '{"prompt":"test"}'`
- E2E: 콘텐츠 업로드 → 요약 완료 → 이미지 자동 생성 → S3 저장 확인
- Frontend: 콘텐츠 상세 페이지 커버 이미지 표시 + 재생성 버튼
- 실패 내성: RunPod 중단 시 요약 정상 완료 확인
- Playwright MCP 브라우저 테스트

### 4. 커밋 & 브랜치 정리

- 별도 피처 브랜치 생성 권장 (예: `feat/cover-image-generation`)
- 커밋 전 사용자 확인 필요 (CLAUDE.md 규칙)

---

## 아키텍처 흐름

```
[콘텐츠 업로드]
    ↓
[ASR/OCR → LLM 요약] (기존 파이프라인)
    ↓
[Worker: llm_processor.py]
    ├─ 요약 결과 S3 저장 ✓
    └─ _generate_cover_image() (non-fatal)
         ↓
    [AI Gateway: POST /v1/images/generations]
         ↓
    [RunPod ComfyUI Serverless (SDXL-Turbo)]
         ↓
    [이미지 bytes → S3 업로드]
         ↓
[publish_llm_completed(result_s3_key, image_s3_key)]
         ↓
[Backend: stream_consumer → DB 저장 (cover_image_key)]
         ↓
[Frontend: ContentCard (썸네일) / SummaryDisplay (상세 + 재생성)]
```

## 참고 자료

- [RunPod worker-comfyui GitHub](https://github.com/runpod-workers/worker-comfyui)
- [RunPod ComfyUI 배포 문서](https://docs.runpod.io/tutorials/serverless/comfyui)
- [ComfyUI SDXL-Turbo 예제](https://comfyanonymous.github.io/ComfyUI_examples/sdturbo/)
- [RunPod API Keys](https://docs.runpod.io/get-started/api-keys)
- 계획 파일: `C:\Users\jg\.claude\plans\glowing-imagining-church.md`
