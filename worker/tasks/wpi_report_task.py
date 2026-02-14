"""WPI AI 리포트 Celery 태스크."""

from typing import Any

from worker.celery_app import celery_app
from worker.logging_config import logger
from worker.telemetry import trace_celery_task


@celery_app.task(
    name="worker.tasks.wpi_report_task.process_wpi_report_task",
    bind=True,
    queue="llm_summary",
)
def process_wpi_report_task(
    self,
    scan_result_id: str,
    messages: list[dict[str, Any]],
):
    """WPI AI 리포트 생성 작업을 실행합니다."""

    from worker.processors.wpi_report_processor import process_wpi_report_job

    logger.info(
        "[Celery WPI Report] Starting task: scan_result_id=%s, task_id=%s, retry=%s",
        scan_result_id,
        self.request.id,
        self.request.retries,
    )

    with trace_celery_task(
        self,
        file_id=scan_result_id,
        pipeline_stage="wpi_report",
    ) as span:
        span.set_attribute("num_messages", len(messages))

        process_wpi_report_job(scan_result_id=scan_result_id, messages=messages)
        return {"status": "success", "scan_result_id": scan_result_id}
