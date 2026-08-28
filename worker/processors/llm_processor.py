"""LLM (요약) 처리 프로세서.

[Phase 1] Worker는 "멍청한 실행기(Dumb Proxy)"가 되었습니다.
Backend에서 완성된 프롬프트(messages)를 받아 LLM에 던지고, Raw 응답만 반환합니다.
결과는 S3에 저장하고 Redis Stream으로 완료를 알립니다.
백엔드 DB에 직접 접근하지 않습니다.
"""

from uuid import uuid4

from worker.config import get_settings
from worker.logging_config import logger
from worker.utils.event_loop import setup_worker_event_loop, cleanup_worker_event_loop
from worker.utils.storage import upload_json
from worker.utils.result_publisher import (
    publish_llm_started,
    publish_llm_completed,
    publish_llm_failed,
)

settings = get_settings()


def process_llm_job(*, file_id: int, messages: list[dict]) -> None:
    """Celery 워커가 호출하는 요약 작업 진입점.

    [Phase 1] 프롬프트 주입 패턴: 완성된 messages 리스트를 받음.

    Args:
        file_id: 파일 ID
        messages: LLM 호출용 완성된 messages 리스트 (Backend에서 생성)
    """
    logger.info("[LLM] ========================================")
    logger.info(f"[LLM] Summary job started: file_id={file_id}")
    logger.info(f"[LLM] Received {len(messages)} messages (Prompt Injection Mode)")

    # 이벤트 루프 설정
    loop = setup_worker_event_loop()

    try:
        loop.run_until_complete(_process_job_async(file_id=file_id, messages=messages))
        logger.info(f"[LLM] OK Summary job completed: file_id={file_id}")
    except Exception as exc:
        logger.error(f"[LLM] ERROR Summary job failed: file_id={file_id}, error={exc}")
        raise
    finally:
        cleanup_worker_event_loop(loop)


async def _process_job_async(
    *,
    file_id: int,
    messages: list[dict],
) -> None:
    """LLM 요약 작업 처리 함수 (async wrapper).

    [Phase 1] 복잡한 로직(청크 분할, 병합, 3단계 파이프라인)을 모두 제거.
    Worker는 messages를 그대로 LLM에 던지고, Raw 응답만 반환합니다.

    Args:
        file_id: 파일 ID
        messages: LLM 호출용 완성된 messages 리스트 (Backend에서 생성)
    """

    # 프롬프트가 비어 있는지 확인
    if not messages:
        raise ValueError("LLM messages are empty")

    logger.info(
        f"[LLM] Processing job (Dumb Proxy Mode): file_id={file_id}, num_messages={len(messages)}"
    )

    # [Phase 1] LLM 호출 (단순화)
    try:
        from worker.pipelines.llm.ai_gateway_client import request_ai_gateway_completion

        # 리소스 획득 이벤트 발행 (LLM 호출 직전)
        publish_llm_started(file_id)
        logger.info(f"[LLM] Published 'started' event for file_id={file_id}")

        logger.info(f"[LLM] Calling LLM with {len(messages)} messages...")

        # LLM 호출 (messages 그대로)
        response = request_ai_gateway_completion(
            settings=get_settings(),
            messages=messages,
            model="auto",
            routing={
                "workload": "summary",
                "reasoning": "low",
                "execution_scope": "local_only",
            },
        )

        logger.info(f"[LLM] LLM response received: {len(response)} chars")

    except Exception as exc:
        logger.error(f"[LLM] LLM call failed: {exc}")
        logger.exception("LLM call failed for file_id={}".format(file_id))

        publish_llm_failed(file_id, error=str(exc))
        raise

    # 결과를 S3에 JSON으로 저장
    logger.info("[LLM] Saving results to S3...")

    # [Phase 1] Raw 응답 그대로 저장 (Backend에서 파싱)
    result_data = {
        "file_id": file_id,
        "raw_response": response,  # Raw LLM 응답 문자열
        "skipped": False,
    }

    result_s3_key = f"results/llm/{file_id}/{uuid4().hex}.json"
    upload_json(result_data, key=result_s3_key)

    logger.info(f"[LLM] Results saved to S3: {result_s3_key}")

    # Redis Stream: 완료 알림
    publish_llm_completed(file_id, result_s3_key=result_s3_key)

    logger.info(
        "[LLM] OK LLM processing completed (Dumb Proxy Mode), result published to stream"
    )
    logger.info("LLM processing completed for file_id={}".format(file_id))
