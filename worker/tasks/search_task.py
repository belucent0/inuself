"""Deep Search Celery 태스크.

검색 결과는 Valkey에 임시 저장됩니다 (TTL 1시간).
비동기 처리가 필요한 경우 사용합니다.
"""
import json
import redis

from worker.celery_app import celery_app
from worker.config import get_settings
from worker.logging_config import logger


# Valkey 키 패턴
SEARCH_RESULT_KEY_PREFIX = "search:result:"
SEARCH_RESULT_TTL = 3600  # 1시간


def _get_redis_client():
    """Redis/Valkey 클라이언트를 반환합니다."""
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def _store_search_result(search_id: str, result: dict) -> None:
    """검색 결과를 Valkey에 저장합니다."""
    client = _get_redis_client()
    key = f"{SEARCH_RESULT_KEY_PREFIX}{search_id}"
    client.setex(key, SEARCH_RESULT_TTL, json.dumps(result, ensure_ascii=False))
    logger.info("[Search] Result stored: search_id=%s, ttl=%ds", search_id, SEARCH_RESULT_TTL)


def get_search_result(search_id: str) -> dict | None:
    """Valkey에서 검색 결과를 조회합니다."""
    client = _get_redis_client()
    key = f"{SEARCH_RESULT_KEY_PREFIX}{search_id}"
    data = client.get(key)
    if data:
        return json.loads(data)
    return None


@celery_app.task(
    name="worker.tasks.search_task.process_search_task",
    bind=True,
    max_retries=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    queue="search",
)
def process_search_task(
    self,
    search_id: str,
    query: str,
    max_results: int = 10,
    categories: str = "general",
    language: str = "ko-KR",
):
    """Deep Search 비동기 처리 태스크.

    Args:
        search_id: 검색 고유 ID (결과 조회용)
        query: 검색 쿼리
        max_results: 최대 검색 결과 수
        categories: 검색 카테고리
        language: 검색 언어
    """
    from worker.pipelines.search.pipeline import deep_search

    logger.info(
        "[Celery Search] Starting task: search_id=%s, query=%r, task_id=%s",
        search_id, query[:50], self.request.id
    )

    # 진행 중 상태 저장
    _store_search_result(search_id, {
        "status": "processing",
        "query": query,
        "search_id": search_id,
    })

    try:
        result = deep_search(
            query=query,
            max_results=max_results,
            categories=categories,
            language=language,
        )

        # 결과 저장
        result_dict = result.to_dict()
        result_dict["status"] = "completed" if not result.error else "failed"
        result_dict["search_id"] = search_id

        _store_search_result(search_id, result_dict)

        logger.info(
            "[Celery Search] Task completed: search_id=%s, sources=%d",
            search_id, result.search_count
        )

        return {"status": "success", "search_id": search_id}

    except Exception as exc:
        is_last_retry = self.request.retries >= self.max_retries

        if is_last_retry:
            logger.error(
                "[Celery Search] Task failed permanently: search_id=%s, error=%s",
                search_id, exc
            )
            _store_search_result(search_id, {
                "status": "failed",
                "query": query,
                "search_id": search_id,
                "error": str(exc),
            })
            return {"status": "failed", "search_id": search_id, "error": str(exc)}
        else:
            logger.warning(
                "[Celery Search] Task failed (retry %d/%d): search_id=%s, error=%s",
                self.request.retries, self.max_retries, search_id, exc
            )
            raise
