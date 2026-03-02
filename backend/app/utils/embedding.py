"""
Embedding 생성 유틸리티

FLM embeddinggemma:300m 모델을 사용하여 텍스트 임베딩을 생성합니다.
Backend → Redis Stream(stream:chat:requests) → Provider Manager → FLM 경로 사용.
"""
import asyncio
import json
import time
import uuid
from loguru import logger

from ..core.redis import get_redis_client

CHAT_STREAM = "stream:chat:requests"
RESPONSE_STREAM = "stream:gpu:responses"


def _generate_request_id() -> str:
    """유니크 request_id 생성."""
    return f"{uuid.uuid4().hex[:16]}_{int(time.time() * 1000)}"


async def create_embedding(
    text: str,
    model: str = "embeddinggemma:300m",
    timeout: float = 15.0,
    max_retries: int = 3  # 시그니처 호환 유지 (내부적으로 미사용)
) -> list[float] | None:
    """텍스트 임베딩 생성 (Redis Stream 경유)

    Args:
        text: 임베딩할 텍스트
        model: 임베딩 모델 (기본: embeddinggemma:300m)
        timeout: 응답 대기 타임아웃 (초)
        max_retries: 하위 호환 파라미터 (미사용)

    Returns:
        list[float]: 768차원 임베딩 벡터
        None: 실패 시
    """
    redis_client = get_redis_client()
    request_id = _generate_request_id()

    request_data = {
        "request_id": request_id,
        "type": "embedding",
        "text": text,
        "model": model,
        "timestamp": str(time.time()),
    }

    try:
        # 요청 전송 전 현재 응답 스트림의 마지막 ID 확인 → 이후 메시지만 읽기 위해
        tail = await redis_client.xrevrange(RESPONSE_STREAM, "+", "-", count=1)
        last_id = tail[0][0] if tail else "0"

        await redis_client.xadd(CHAT_STREAM, request_data)
        logger.debug(f"Embedding request sent: request_id={request_id}, {len(text)} chars")
    except Exception as e:
        logger.error(f"Failed to send embedding request to Redis Stream: {e}")
        return None

    # XREAD로 응답 대기
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            messages = await redis_client.xread(
                {RESPONSE_STREAM: last_id},
                count=10,
                block=1000,
            )

            if messages:
                for _stream_name, stream_messages in messages:
                    for message_id, message_data in stream_messages:
                        last_id = message_id

                        if message_data.get("request_id") != request_id:
                            continue

                        # processing_started 이벤트는 건너뜀
                        if message_data.get("event") == "processing_started":
                            continue

                        if "error" in message_data:
                            logger.error(f"Embedding error from provider: {message_data['error']}")
                            return None

                        if "result" in message_data:
                            result = json.loads(message_data["result"])
                            embedding = result.get("data", [{}])[0].get("embedding", [])

                            if len(embedding) > 0:
                                logger.debug(f"Embedding received: {len(text)} chars → {len(embedding)} dims")
                                return embedding
                            else:
                                logger.warning(f"Empty embedding returned: {result}")
                                return None

        except Exception as e:
            logger.warning(f"Redis read error, retrying: {e}")
            await asyncio.sleep(1)
            continue

    logger.error(f"Embedding timeout after {timeout}s for request_id={request_id}")
    return None


async def create_embeddings_batch(
    texts: list[str],
    model: str = "embeddinggemma:300m",
    batch_size: int = 10,
    delay: float = 0.1
) -> list[list[float] | None]:
    """여러 텍스트의 임베딩을 배치로 생성

    Args:
        texts: 임베딩할 텍스트 리스트
        model: 임베딩 모델
        batch_size: 동시 처리할 최대 개수
        delay: 요청 간 지연 시간 (초)

    Returns:
        list[list[float] | None]: 임베딩 리스트 (실패 시 None)
    """
    embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]

        for text in batch:
            embedding = await create_embedding(text, model=model)
            embeddings.append(embedding)

            if delay > 0:
                await asyncio.sleep(delay)

        if i + batch_size < len(texts):
            logger.info(f"Batch progress: {i + len(batch)}/{len(texts)}")

    return embeddings


async def warmup_embedding_service(timeout: float = 30.0) -> bool:
    """FLM embedding 서비스 준비 확인 (Redis Stream 경유)

    Args:
        timeout: 최대 대기 시간 (초)

    Returns:
        bool: 준비 완료 여부
    """
    logger.info("Warming up FLM embedding service via Redis Stream...")

    embedding = await create_embedding("warmup", timeout=timeout)

    if embedding:
        logger.info("✅ FLM embedding service is ready!")
        return True
    else:
        logger.error("❌ Failed to warm up FLM embedding service")
        return False


# 동기 래퍼 (필요 시 사용)
def create_embedding_sync(text: str, model: str = "embeddinggemma:300m") -> list[float] | None:
    """동기 버전의 create_embedding

    Note:
        - asyncio 이벤트 루프가 없는 환경에서 사용
        - 가능하면 비동기 버전(create_embedding) 사용 권장
    """
    return asyncio.run(create_embedding(text, model=model))
