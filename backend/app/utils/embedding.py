"""
Embedding 생성 유틸리티

FLM embeddinggemma:300m 모델을 사용하여 텍스트 임베딩을 생성합니다.
Provider-Manager의 On-Demand 로딩을 고려한 Retry 로직 포함.
"""
import asyncio
import httpx
from loguru import logger


async def create_embedding(
    text: str,
    model: str = "embeddinggemma:300m",
    timeout: float = 30.0,
    max_retries: int = 3
) -> list[float] | None:
    """텍스트 임베딩 생성

    Args:
        text: 임베딩할 텍스트
        model: 임베딩 모델 (기본: embeddinggemma:300m)
        timeout: HTTP 타임아웃 (초)
        max_retries: 최대 재시도 횟수

    Returns:
        list[float]: 768차원 임베딩 벡터
        None: 실패 시

    Note:
        - Provider-Manager가 FLM 서버를 On-Demand로 로드 (~11초 소요)
        - 첫 요청 실패 시 자동으로 로딩 대기 후 재시도
    """
    embedding_url = "http://localhost:11435/v1/embeddings"

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_retries):
            try:
                response = await client.post(
                    embedding_url,
                    json={
                        "model": model,
                        "input": text,
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    # OpenAI 호환 포맷: {"data": [{"embedding": [...]}]}
                    embedding = data.get("data", [{}])[0].get("embedding", [])

                    if len(embedding) == 768:
                        logger.debug(f"Embedding generated: {len(text)} chars → 768 dims")
                        return embedding
                    else:
                        logger.warning(f"Unexpected embedding dimension: {len(embedding)}")
                        return None
                else:
                    logger.error(f"Embedding API error: {response.status_code}")

            except httpx.ConnectError as e:
                # Provider-Manager가 FLM 로드 중일 수 있음
                if attempt == 0:
                    logger.info("FLM server not ready, waiting for On-Demand load... (~12s)")
                    await asyncio.sleep(12)  # FLM 로딩 대기
                else:
                    logger.warning(f"Connection failed (attempt {attempt + 1}/{max_retries}): {e}")
                    await asyncio.sleep(2)

            except httpx.TimeoutException as e:
                logger.warning(f"Timeout (attempt {attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Embedding generation failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)

    logger.error(f"Failed to generate embedding after {max_retries} attempts")
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

    Note:
        - API 레이트 리미트 방지를 위해 delay 사용
        - 첫 요청에서 FLM 로딩 시간 자동 대기
    """
    embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]

        # 배치 내 텍스트들을 순차 처리
        for text in batch:
            embedding = await create_embedding(text, model=model)
            embeddings.append(embedding)

            # API 레이트 리미트 방지
            if delay > 0:
                await asyncio.sleep(delay)

        if i + batch_size < len(texts):
            logger.info(f"Batch progress: {i + len(batch)}/{len(texts)}")

    return embeddings


async def warmup_embedding_service(timeout: float = 30.0) -> bool:
    """FLM embedding 서비스 준비 대기

    Provider-Manager가 FLM 서버를 로드할 때까지 대기합니다.

    Args:
        timeout: 최대 대기 시간 (초)

    Returns:
        bool: 준비 완료 여부
    """
    logger.info("Warming up FLM embedding service...")

    # 더미 텍스트로 서비스 활성화
    dummy_text = "warmup"
    embedding = await create_embedding(dummy_text, timeout=timeout)

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
