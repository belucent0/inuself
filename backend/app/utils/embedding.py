"""Embedding 생성 유틸리티.

Backend → ai-gateway (`/v1/embeddings`) → ai-embedding 컨테이너(embeddinggemma-300m) httpx 직결.
v1.1.0의 Redis Stream + Provider Manager 경로는 v1.2.0에서 폐기됨.
"""
import asyncio
import os

import httpx
from loguru import logger


AI_GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "http://ai-gateway:4000")
AI_GATEWAY_API_KEY = os.getenv("AI_GATEWAY_API_KEY", "")
DEFAULT_MODEL = "embeddinggemma-300m"


async def create_embedding(
    text: str,
    model: str = DEFAULT_MODEL,
    timeout: float = 15.0,
    max_retries: int = 3,  # 시그니처 호환 유지 (내부적으로 미사용)
) -> list[float] | None:
    """텍스트 임베딩 생성 (ai-gateway 경유).

    Args:
        text: 임베딩할 텍스트
        model: 임베딩 모델 (기본: embeddinggemma-300m)
        timeout: HTTP 타임아웃 (초)
        max_retries: 하위 호환 파라미터 (미사용)

    Returns:
        list[float]: 768차원 임베딩 벡터
        None: 실패 시
    """
    headers = {}
    if AI_GATEWAY_API_KEY:
        headers["Authorization"] = f"Bearer {AI_GATEWAY_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{AI_GATEWAY_URL}/v1/embeddings",
                headers=headers,
                json={"input": text, "model": model},
            )
            response.raise_for_status()
            data = response.json()

            embedding = data.get("data", [{}])[0].get("embedding", [])
            if not embedding:
                logger.warning(f"[Embedding] Empty embedding returned: {data}")
                return None

            logger.debug(f"[Embedding] {len(text)} chars → {len(embedding)} dims")
            return embedding

    except httpx.TimeoutException:
        logger.error(f"[Embedding] Timeout after {timeout}s ({len(text)} chars)")
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"[Embedding] Upstream {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"[Embedding] Failed: {e}")
        return None


async def create_embeddings_batch(
    texts: list[str],
    model: str = DEFAULT_MODEL,
    batch_size: int = 10,
    delay: float = 0.1,
) -> list[list[float] | None]:
    """여러 텍스트의 임베딩을 배치로 생성.

    Args:
        texts: 임베딩할 텍스트 리스트
        model: 임베딩 모델
        batch_size: 동시 처리할 최대 개수
        delay: 요청 간 지연 시간 (초)

    Returns:
        list[list[float] | None]: 임베딩 리스트 (실패 시 None)
    """
    embeddings: list[list[float] | None] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]

        for text in batch:
            embedding = await create_embedding(text, model=model)
            embeddings.append(embedding)

            if delay > 0:
                await asyncio.sleep(delay)

        if i + batch_size < len(texts):
            logger.info(f"[Embedding] Batch progress: {i + len(batch)}/{len(texts)}")

    return embeddings


async def warmup_embedding_service(timeout: float = 30.0) -> bool:
    """ai-embedding 서비스 준비 확인 (warmup ping).

    Args:
        timeout: HTTP 타임아웃 (초)

    Returns:
        bool: 준비 완료 여부
    """
    logger.info("[Embedding] Warming up ai-embedding via ai-gateway...")
    embedding = await create_embedding("warmup", timeout=timeout)
    if embedding:
        logger.info("[Embedding] ai-embedding service is ready")
        return True
    logger.error("[Embedding] Failed to warm up ai-embedding service")
    return False


def create_embedding_sync(text: str, model: str = DEFAULT_MODEL) -> list[float] | None:
    """동기 버전의 create_embedding.

    Note:
        - asyncio 이벤트 루프가 없는 환경에서 사용
        - 가능하면 비동기 버전(create_embedding) 사용 권장
    """
    return asyncio.run(create_embedding(text, model=model))
