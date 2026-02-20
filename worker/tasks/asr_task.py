"""ASR Celery task."""

from worker.celery_app import celery_app
from worker.logging_config import logger
from worker.utils.result_publisher import publish_asr_failed
from worker.telemetry import trace_celery_task


@celery_app.task(
    name="worker.tasks.asr_task.process_asr_task",
    bind=True,
    max_retries=3,
    # Manual retry control for clear terminal-failure handling.
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
    **kwargs,
):
    """Run ASR task and publish terminal failure events when retries are exhausted."""
    from worker.processors.asr_processor import process_transcription_job
    from opentelemetry.trace import Status, StatusCode

    logger.info(
        "[Celery ASR] Starting task: file_id={}, task_id={}, accuracy_mode={}, retry={}",
        file_id,
        self.request.id,
        accuracy_mode,
        self.request.retries,
    )

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
            logger.error(
                "[Celery ASR] Permanent failure (no retry): file_id={}, error={}",
                file_id,
                exc,
            )
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            publish_asr_failed(file_id, error=str(exc))
            return {"status": "failed", "file_id": file_id, "error": str(exc)}

        except Exception as exc:
            # Celery increments retries per attempt. If this attempt is already the last,
            # publish terminal failure explicitly instead of delegating to retry().
            is_last_retry = self.request.retries >= self.max_retries
            if is_last_retry:
                logger.error(
                    "[Celery ASR] Task failed permanently (max retries {}): file_id={}, error={}",
                    self.max_retries,
                    file_id,
                    exc,
                )
                span.set_status(Status(StatusCode.ERROR, "Max retries exceeded"))
                span.record_exception(exc)
                publish_asr_failed(file_id, error=str(exc))
                return {"status": "failed", "file_id": file_id, "error": str(exc)}

            logger.warning(
                "[Celery ASR] Transient failure (retry {}/{}): file_id={}, error={}",
                self.request.retries,
                self.max_retries,
                file_id,
                exc,
            )
            span.record_exception(exc)
            raise self.retry(exc=exc)
