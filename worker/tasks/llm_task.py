"""LLM Celery 태스크.

워커는 DB에 직접 접근하지 않습니다.
결과는 Redis Stream으로 발행하고, 백엔드 StreamConsumer가 DB에 저장합니다.
"""

from worker.celery_app import celery_app
from worker.logging_config import logger
from worker.telemetry import trace_celery_task


@celery_app.task(
    name="worker.tasks.llm_task.process_llm_task",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    queue="llm_summary",  # Backend task_queue_adapter와 일치
)
def process_llm_task(
    self,
    file_id: int,
    messages: list[dict],  # [Phase 1] 프롬프트 주입: 완성된 messages 리스트를 받음
):
    """LLM 요약 작업 처리 Celery 태스크.

    [Phase 1] Worker는 "멍청한 실행기(Dumb Proxy)"가 되었습니다.
    프롬프트 생성, 파싱 등 모든 비즈니스 로직은 Backend에 있습니다.
    Worker는 받은 messages를 그대로 LLM에 던지고, Raw 응답만 반환합니다.

    Args:
        file_id: 파일 ID
        messages: LLM 호출용 완성된 messages 리스트 (Backend에서 생성)
    """
    from worker.processors.llm_processor import process_llm_job
    from worker.utils.result_publisher import publish_llm_failed

    logger.info(
        "[Celery LLM] Starting task (Prompt Injection): file_id={}, task_id={}, retry={}",
        file_id,
        self.request.id,
        self.request.retries,
    )

    # OpenTelemetry trace context 활성화 (분산 추적 연결)
    with trace_celery_task(self, file_id=file_id, pipeline_stage="llm_summary") as span:
        span.set_attribute("prompt_injection", True)
        span.set_attribute("num_messages", len(messages))

        try:
            process_llm_job(file_id=file_id, messages=messages)

            logger.info("[Celery LLM] Task completed: file_id={}", file_id)
            return {"status": "success", "file_id": file_id}

        except Exception as exc:
            error_str = str(exc).lower()

            # 재시도해도 소용없는 에러들
            no_retry_keywords = [
                "context",
                "token",
                "overflow",
                "400 bad request",
                "failed to load model",
                "gpu",
                "vram",
                "memory",
                "failed to initialize",
                "allocation failed",
                "outofdevicememory",
            ]

            is_last_retry = self.request.retries >= self.max_retries
            should_not_retry = any(
                keyword in error_str for keyword in no_retry_keywords
            )

            if should_not_retry or is_last_retry:
                # 최종 실패 - Redis Stream으로 실패 발행
                logger.error(
                    "[Celery LLM] Task failed permanently: file_id={}, error={}",
                    file_id,
                    exc,
                )
                publish_llm_failed(file_id, error=str(exc))
                return {"status": "failed", "file_id": file_id, "error": str(exc)}
            else:
                # 재시도 가능
                logger.warning(
                    "[Celery LLM] Task failed (retry {}/{}, will retry): file_id={}, error={}",
                    self.request.retries,
                    self.max_retries,
                    file_id,
                    exc,
                )
                raise  # autoretry가 처리
