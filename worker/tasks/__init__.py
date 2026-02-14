"""Celery 태스크 모듈."""

from .asr_task import process_asr_task
from .llm_task import process_llm_task
from .ocr_task import process_ocr_task
from .wpi_report_task import process_wpi_report_task

__all__ = [
    "process_asr_task",
    "process_llm_task",
    "process_ocr_task",
    "process_wpi_report_task",
]
