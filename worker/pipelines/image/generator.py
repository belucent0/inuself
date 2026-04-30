"""이미지 생성 파이프라인.

RunPod Serverless를 통해 콘텐츠 커버 이미지를 생성합니다.
AI Gateway의 /v1/images/generations 엔드포인트를 호출합니다.
"""

import base64
import logging

import httpx

from worker.config import get_settings

logger = logging.getLogger(__name__)

# 이미지 프롬프트 템플릿
_PROMPT_TEMPLATE = (
    "A clean, modern digital illustration representing: {title}. "
    "Keywords: {keywords}. "
    "Style: professional, minimalist, soft gradient background, no text overlay."
)


def generate_content_image(
    *,
    title: str,
    keywords: str,
    summary: str = "",
) -> bytes | None:
    """콘텐츠 요약 정보로 커버 이미지를 생성합니다.

    Args:
        title: 콘텐츠 제목
        keywords: 쉼표로 구분된 키워드
        summary: 요약 텍스트 (프롬프트 보강용)

    Returns:
        PNG 이미지 바이트. 실패 시 None.
    """
    settings = get_settings()
    gateway_url = settings.ai_gateway_url.rstrip("/")

    prompt = _PROMPT_TEMPLATE.format(
        title=title[:200],
        keywords=keywords[:200],
    )

    logger.info(f"[ImageGen] Generating cover image for: {title[:50]}...")

    try:
        with httpx.Client(timeout=90.0) as client:
            resp = client.post(
                f"{gateway_url}/v1/images/generations",
                json={
                    "prompt": prompt,
                    "model": "sd-turbo",
                    "n": 1,
                    "size": "512x512",
                    "response_format": "b64_json",
                },
                headers={"Authorization": f"Bearer {settings.ai_gateway_api_key}"},
            )
            resp.raise_for_status()
            result = resp.json()

        data = result.get("data", [])
        if not data:
            logger.warning("[ImageGen] No image data in response")
            return None

        image_b64 = data[0].get("b64_json")
        if not image_b64:
            logger.warning("[ImageGen] No b64_json in response data")
            return None

        image_bytes = base64.b64decode(image_b64)
        logger.info(f"[ImageGen] Image generated successfully: {len(image_bytes)} bytes")
        return image_bytes

    except httpx.TimeoutException:
        logger.warning("[ImageGen] Request timed out (RunPod cold start?)")
        return None
    except httpx.HTTPStatusError as e:
        logger.warning(f"[ImageGen] HTTP error: {e.response.status_code} - {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.warning(f"[ImageGen] Unexpected error: {e}")
        return None
