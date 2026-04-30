"""이미지 생성 라우트.

POST /v1/images/generations — RunPod Serverless (ComfyUI) 이미지 생성
OpenAI Images API 호환 엔드포인트.

ComfyUI 워크플로우를 RunPod 서버리스로 전송하여 SDXL-Turbo 이미지를 생성합니다.
"""

import logging
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import RUNPOD_API_KEY, RUNPOD_IMAGE_BASE_URL, RUNPOD_IMAGE_MODEL

logger = logging.getLogger(__name__)
router = APIRouter()

# RunPod 서버리스 타임아웃 (콜드 스타트 ~20초 + 생성 ~2초)
_RUNPOD_TIMEOUT = 90.0


def _build_comfyui_workflow(
    prompt: str,
    negative_prompt: str = "",
    width: int = 512,
    height: int = 512,
    steps: int = 4,
    cfg: float = 1.0,
    seed: int = -1,
) -> dict:
    """SDXL-Turbo용 ComfyUI API 포맷 워크플로우를 생성합니다.

    노드 구성:
      4: CheckpointLoaderSimple → 모델/CLIP/VAE 로드
      6: CLIPTextEncode (positive) → 프롬프트 인코딩
      7: CLIPTextEncode (negative) → 네거티브 프롬프트
      5: EmptyLatentImage → 빈 latent 생성
      3: KSampler → 이미지 생성 (euler, 4스텝, CFG 1.0)
      8: VAEDecode → latent → 이미지 디코딩
      9: SaveImage → 결과 저장 (RunPod worker가 base64로 반환)
    """
    import random

    if seed < 0:
        seed = random.randint(0, 2**32 - 1)

    return {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": "sd_xl_turbo_1.0_fp16.safetensors",
            },
            "_meta": {"title": "Load Checkpoint"},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt,
                "clip": ["4", 1],
            },
            "_meta": {"title": "CLIP Text Encode (Positive)"},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": negative_prompt or "blurry, low quality, text, watermark",
                "clip": ["4", 1],
            },
            "_meta": {"title": "CLIP Text Encode (Negative)"},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1,
            },
            "_meta": {"title": "Empty Latent Image"},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
            "_meta": {"title": "KSampler"},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["3", 0],
                "vae": ["4", 2],
            },
            "_meta": {"title": "VAE Decode"},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "ComfyUI",
                "images": ["8", 0],
            },
            "_meta": {"title": "Save Image"},
        },
    }


@router.post("/v1/images/generations")
async def images_generations(request: Request):
    """OpenAI-compatible 이미지 생성 엔드포인트.

    ComfyUI 워크플로우를 RunPod Serverless로 전송합니다.
    """
    body = await request.json()
    prompt = body.get("prompt", "")
    n = body.get("n", 1)
    size = body.get("size", "512x512")
    negative_prompt = body.get("negative_prompt", "")
    response_format = body.get("response_format", "b64_json")

    if not prompt:
        return JSONResponse(
            {"error": {"message": "prompt is required", "type": "invalid_request_error"}},
            status_code=400,
        )

    if not RUNPOD_IMAGE_BASE_URL:
        return JSONResponse(
            {"error": {"message": "Image generation not configured (RUNPOD_IMAGE_BASE_URL missing)", "type": "server_error"}},
            status_code=503,
        )

    # 크기 파싱
    try:
        width, height = (int(x) for x in size.split("x"))
    except (ValueError, AttributeError):
        width, height = 512, 512

    logger.info(f"[Image] ComfyUI SDXL-Turbo generation: size={width}x{height}, n={n}")

    try:
        images = []
        for _ in range(n):
            image_b64 = await _generate_via_comfyui(
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
            )
            images.append({"b64_json": image_b64})

        return JSONResponse({
            "created": int(time.time()),
            "data": images,
        })

    except httpx.TimeoutException:
        logger.error("[Image] RunPod ComfyUI request timed out")
        return JSONResponse(
            {"error": {"message": "Image generation timed out", "type": "timeout_error"}},
            status_code=504,
        )
    except Exception as e:
        logger.error(f"[Image] ComfyUI generation failed: {e}")
        return JSONResponse(
            {"error": {"message": str(e), "type": "server_error"}},
            status_code=500,
        )


async def _generate_via_comfyui(
    prompt: str,
    negative_prompt: str = "",
    width: int = 512,
    height: int = 512,
) -> str:
    """RunPod ComfyUI 서버리스 워커를 통해 이미지 생성.

    Returns:
        base64 인코딩된 이미지 문자열
    """
    workflow = _build_comfyui_workflow(
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=4,      # SDXL-Turbo 최적
        cfg=1.0,       # Turbo 모델은 낮은 CFG
    )

    runpod_payload = {
        "input": {
            "workflow": workflow,
        }
    }

    async with httpx.AsyncClient(timeout=_RUNPOD_TIMEOUT) as client:
        resp = await client.post(
            f"{RUNPOD_IMAGE_BASE_URL}/runsync",
            json=runpod_payload,
            headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"},
        )
        resp.raise_for_status()
        result = resp.json()

    status = result.get("status")
    if status != "COMPLETED":
        error_msg = result.get("error", f"RunPod job status: {status}")
        raise RuntimeError(f"ComfyUI generation failed: {error_msg}")

    output = result.get("output", {})

    # RunPod worker-comfyui 출력 포맷:
    # { "images": [{ "filename": "...", "type": "base64", "data": "..." }] }
    output_images = output.get("images", [])
    if not output_images:
        raise RuntimeError(f"No images in ComfyUI response. Output keys: {list(output.keys())}")

    first_image = output_images[0]
    image_b64 = first_image.get("data", "")

    if not image_b64:
        raise RuntimeError("Empty image data in ComfyUI response")

    # data:image/png;base64, 접두사 제거 (있는 경우)
    if image_b64.startswith("data:"):
        image_b64 = image_b64.split(",", 1)[1]

    return image_b64
