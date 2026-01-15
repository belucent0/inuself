"""ASR Celery 태스크.

LiteLLM 중앙집중 리소스 관리를 사용합니다.
- 리소스 획득/해제는 litellm_audio_client.py에서 자동 처리
- 실패 시 자동 재시도: autoretry_for, retry_backoff
- FAILED 상태: 마지막 재시도 실패 시 Redis Stream으로 발행
"""
from worker.celery_app import celery_app
from worker.logging_config import logger
from worker.utils.result_publisher import publish_asr_failed


@celery_app.task(
    name="worker.tasks.asr_task.process_asr_task",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    queue="asr",
)
def process_asr_task(
    self,
    file_id: int,
    storage_key: str,
    original_filename: str,
    model_size: str,
    processing_mode: str,
    num_asr_chunks: int,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    accuracy_mode: str = "speed",
    **kwargs
):
    """
    ASR 작업 처리 Celery 태스크.
    
    Args:
        accuracy_mode: 전사 모드 ('speed' - FLM/NPU, 'accuracy' - whisper.cpp/GPU)
    
    락 없이 순수하게 작업만 처리합니다.
    실패 시 Celery autoretry로 자동 재시도됩니다.
    마지막 재시도 실패 시에만 DB에 FAILED 상태를 저장합니다.
    """
    from worker.processors.asr_processor import process_transcription_job
    
    logger.info(
        "[Celery ASR] Starting task: file_id={}, task_id={}, accuracy_mode={}, retry={}",
        file_id, self.request.id, accuracy_mode, self.request.retries
    )
    
    try:
        process_transcription_job(
            file_id=file_id,
            storage_key=storage_key,
            original_filename=original_filename,
            model_size=model_size,
            processing_mode=processing_mode,
            num_asr_chunks=num_asr_chunks,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            accuracy_mode=accuracy_mode,
        )
        
        logger.info("[Celery ASR] Task completed: file_id={}", file_id)
        return {"status": "success", "file_id": file_id}
        
    except FileNotFoundError as exc:
        # 파일 없음은 재시도해도 소용없음 - 즉시 실패 발행
        logger.error("[Celery ASR] File not found (no retry): file_id={}, error={}", file_id, exc)
        publish_asr_failed(file_id, error=str(exc))
        return {"status": "failed", "file_id": file_id, "error": str(exc)}
    except Exception as exc:
        # 마지막 재시도인지 확인
        is_last_retry = self.request.retries >= self.max_retries
        
        if is_last_retry:
            # 마지막 재시도 실패 - Redis Stream으로 실패 발행
            logger.error(
                "[Celery ASR] Task failed permanently (max retries {}): file_id={}, error={}",
                self.max_retries, file_id, exc
            )
            publish_asr_failed(file_id, error=str(exc))
            # autoretry가 더 이상 재시도하지 않으므로 결과 반환
            return {"status": "failed", "file_id": file_id, "error": str(exc)}
        else:
            # 재시도 가능 - 상태는 변경하지 않고 예외만 던짐
            logger.warning(
                "[Celery ASR] Task failed (retry {}/{}, will retry): file_id={}, error={}",
                self.request.retries, self.max_retries, file_id, exc
            )
            raise  # autoretry가 처리
