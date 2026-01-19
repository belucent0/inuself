"""ASR Celery 태스크.

LiteLLM 중앙집중 리소스 관리를 사용합니다.
- 리소스 획득/해제는 litellm_audio_client.py에서 자동 처리
- 실패 시 자동 재시도: autoretry_for, retry_backoff
- FAILED 상태: 마지막 재시도 실패 시 Redis Stream으로 발행
"""
from worker.celery_app import celery_app
from worker.logging_config import logger
from worker.utils.result_publisher import publish_asr_failed
from worker.telemetry import trace_celery_task


@celery_app.task(
    name="worker.tasks.asr_task.process_asr_task",
    bind=True,
    max_retries=3,
    # autoretry_for=(Exception,), # 수동 제어를 위해 비활성화
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

    Resilient Retry Policy (탄력적 재시도 정책):
    1. Permanent Failure (FileNotFound, ValueError): 즉시 실패 처리 (재시도 X)
    2. Transient Failure (Timeout, Connection): 조용히 재시도 (상태 유지)
    3. Unknown Failure: 재시도 수행
    """
    from worker.processors.asr_processor import process_transcription_job
    from celery.exceptions import MaxRetriesExceededError
    from opentelemetry.trace import Status, StatusCode

    logger.info(
        "[Celery ASR] Starting task: file_id={}, task_id={}, accuracy_mode={}, retry={}",
        file_id, self.request.id, accuracy_mode, self.request.retries
    )

    # OpenTelemetry trace context 활성화
    with trace_celery_task(self, file_id=file_id, pipeline_stage="asr") as span:
        span.set_attribute("accuracy_mode", accuracy_mode)
        span.set_attribute("processing_mode", processing_mode)

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

        except (FileNotFoundError, ValueError, TypeError) as exc:
            # [유형 1] 영구적 실패 (Permanent Failure)
            # 재시도해도 성공할 수 없는 오류들 -> 즉시 실패 처리
            logger.error("[Celery ASR] Permanent failure (no retry): file_id={}, error={}", file_id, exc)
            
            # Span 상태 명시적 Error 설정
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            
            # DB 상태 FAILED 변경
            publish_asr_failed(file_id, error=str(exc))
            return {"status": "failed", "file_id": file_id, "error": str(exc)}

        except Exception as exc:
            # [유형 2] 일시적/알 수 없는 오류 (Transient Failure)
            # 재시도하면 성공할 가능성이 있는 오류들 -> 재시도 수행
            try:
                # 타임아웃 등은 경고 레벨로 로깅
                logger.warning(
                    "[Celery ASR] Transient failure (retry {}/{}): file_id={}, error={}",
                    self.request.retries, self.max_retries, file_id, exc
                )
                
                # Span에 에러 기록 (재시도 시도 실패임)
                span.record_exception(exc)
                
                # 재시도 수행 (DB 상태는 PROCESSING 유지)
                raise self.retry(exc=exc)
                
            except MaxRetriesExceededError:
                # [유형 3] 재시도 횟수 초과 (Final Failure)
                logger.error(
                    "[Celery ASR] Task failed permanently (max retries {}): file_id={}, error={}",
                    self.max_retries, file_id, exc
                )
                
                # Span 상태 Error 설정
                span.set_status(Status(StatusCode.ERROR, "Max retries exceeded"))
                
                # DB 상태 FAILED 변경 (이제서야 실패로 처리)
                publish_asr_failed(file_id, error=str(exc))
                return {"status": "failed", "file_id": file_id, "error": str(exc)}
