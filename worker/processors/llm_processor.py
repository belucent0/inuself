"""LLM (요약) 처리 프로세서.

이 모듈은 전사된 텍스트를 LLM을 사용하여 요약합니다.
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


def process_llm_job(*, file_id: int, text_to_summarize: str) -> None:
    """Celery 워커가 호출하는 요약 작업 진입점.
    
    Args:
        file_id: 파일 ID
        text_to_summarize: 요약할 텍스트 (transcription 또는 OCR 결과)
    """
    logger.info("[LLM] ========================================")
    logger.info(f"[LLM] Summary job started: file_id={file_id}")
    logger.info(f"[LLM] Text length: {len(text_to_summarize)} chars")
    
    # 이벤트 루프 설정
    loop = setup_worker_event_loop()
    
    try:
        loop.run_until_complete(_process_job(file_id=file_id, text_to_summarize=text_to_summarize))
        logger.info(f"[LLM] OK Summary job completed: file_id={file_id}")
    except Exception as exc:
        logger.error(f"[LLM] ERROR Summary job failed: file_id={file_id}, error={exc}")
        raise
    finally:
        cleanup_worker_event_loop(loop)


async def _process_job(*, file_id: int, text_to_summarize: str) -> None:
    """LLM 요약 작업 처리 함수."""
    
    if not text_to_summarize or not text_to_summarize.strip():
        logger.warning("[LLM] Text to summarize is empty, skipping: file_id={}", file_id)
        publish_llm_started(file_id)
        result_data = {
            "file_id": file_id,
            "title": "",
            "summary_md": "",
            "skipped": True,
            "skip_reason": "empty_text",
        }
        result_s3_key = f"results/llm/{file_id}/{uuid4().hex}.json"
        upload_json(result_data, key=result_s3_key)

        publish_llm_completed(file_id, result_s3_key=result_s3_key)
        return
    
    try:
        logger.info("[LLM] Starting LLM summarization...")

        from worker.pipelines.llm.summarizer import summarize_transcription, sanitize_summary_output

        def on_resource_acquired():
            publish_llm_started(file_id)
            logger.info(f"[LLM] Resource acquired, published 'started' event for file_id={file_id}")

        # 요약 생성 + 제목 추출 (한 번에 처리)
        title, summary_md = summarize_transcription(text_to_summarize, on_resource_acquired=on_resource_acquired)
        summary_md = sanitize_summary_output(summary_md, text_to_summarize)
        logger.info(f"[LLM] Summarization completed: title='{title}', summary_length={len(summary_md)}")
        
    except Exception as exc:
        logger.error(f"[LLM] Summarization failed: {exc}")
        logger.exception("LLM summarization failed for file_id={}", file_id)
        
        publish_llm_failed(file_id, error=str(exc))
        raise
    
    result_data = {
        "file_id": file_id,
        "title": title,
        "summary_md": summary_md,
        "skipped": False,
    }
    
    result_s3_key = f"results/llm/{file_id}/{uuid4().hex}.json"
    upload_json(result_data, key=result_s3_key)
    
    logger.info(f"[LLM] Results saved to S3: {result_s3_key}")
    
    publish_llm_completed(file_id, result_s3_key=result_s3_key)
    
    logger.info("[LLM] OK LLM processing completed, result published to stream")
