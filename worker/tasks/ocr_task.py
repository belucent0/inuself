"""OCR Celery 태스크.

워커는 DB에 직접 접근하지 않습니다.
결과는 Redis Stream으로 발행하고, 백엔드 StreamConsumer가 DB에 저장합니다.
"""
from worker.celery_app import celery_app
from worker.logging_config import logger


@celery_app.task(
    name="worker.tasks.ocr_task.process_ocr_task",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    queue="ocr",
)
def process_ocr_task(
    self,
    file_id: int,
    image_s3_keys: list[str],
    ocr_mode: str = "document",
    ocr_accuracy_mode: str = "speed",
):
    """OCR 작업 처리 Celery 태스크.
    
    Args:
        file_id: 파일 ID
        image_s3_keys: 이미지 S3 경로 목록 (백엔드에서 전처리된 이미지들)
        ocr_mode: OCR 모드 ("document" 또는 "portray")
        ocr_accuracy_mode: OCR 정확도 모드 ("speed" 또는 "accuracy")
    """
    from worker.processors.ocr_processor import process_ocr_job
    from worker.utils.result_publisher import publish_ocr_failed
    
    logger.info(
        "[Celery OCR] Starting task: file_id={}, task_id={}, images={}, ocr_mode={}, ocr_accuracy_mode={}, retry={}",
        file_id, self.request.id, len(image_s3_keys), ocr_mode, ocr_accuracy_mode, self.request.retries
    )
    
    try:
        process_ocr_job(
            file_id=file_id,
            image_s3_keys=image_s3_keys,
            ocr_mode=ocr_mode,
            ocr_accuracy_mode=ocr_accuracy_mode,
        )
        
        logger.info("[Celery OCR] Task completed: file_id={}", file_id)
        return {"status": "success", "file_id": file_id}
        
    except Exception as exc:
        error_str = str(exc).lower()
        
        # 재시도해도 소용없는 에러들
        no_retry_keywords = [
            "file not found", "not found",
            "gpu", "vram", "memory", "allocation failed",
        ]
        
        is_last_retry = self.request.retries >= self.max_retries
        should_not_retry = any(keyword in error_str for keyword in no_retry_keywords)
        
        if should_not_retry or is_last_retry:
            # 최종 실패 - Redis Stream으로 실패 발행
            logger.error(
                "[Celery OCR] Task failed permanently: file_id={}, error={}",
                file_id, exc
            )
            publish_ocr_failed(file_id, error=str(exc))
            return {"status": "failed", "file_id": file_id, "error": str(exc)}
        else:
            # 재시도 가능
            logger.warning(
                "[Celery OCR] Task failed (retry {}/{}, will retry): file_id={}, error={}",
                self.request.retries, self.max_retries, file_id, exc
            )
            raise  # autoretry가 처리
