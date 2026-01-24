"""Celery 애플리케이션 설정."""
import os
import logging

# ==========================================
# OpenTelemetry 초기화 (다른 import 전에 실행)
# HTTPX instrumentor가 모든 httpx 호출에 적용되도록 함
# ==========================================
def _early_telemetry_init():
    """모듈 로드 시점에 telemetry 초기화 (instrumentor 사전 적용)."""
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
        logging.getLogger(__name__).info("[Telemetry] HTTPX instrumented (early init)")
    except Exception as e:
        logging.getLogger(__name__).debug(f"[Telemetry] Early HTTPX instrument failed: {e}")

# 다른 모듈 import 전에 httpx instrumentor 적용
_early_telemetry_init()

from celery import Celery
from celery.signals import (
    worker_process_init,
    after_setup_logger,
    after_setup_task_logger,
    task_failure,
    task_revoked,
)

from .config import get_settings
from .logging_config import apply_celery_logging_format

settings = get_settings()

# Celery 앱 생성
celery_app = Celery(
    "torch_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "worker.tasks.asr_task",
        "worker.tasks.llm_task",
        "worker.tasks.ocr_task",
        "worker.tasks.cleanup_task",
    ],
)

# Celery 설정
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1시간 제한
    task_soft_time_limit=3300,  # 55분 soft limit
    worker_prefetch_multiplier=1,  # 한 번에 1개 작업만 가져옴
    worker_max_tasks_per_child=100,  # 100개 작업 후 자동 재시작
    result_expires=3600,  # 결과 1시간 보관
    task_acks_late=True,  # 작업 완료 후 ack
    task_reject_on_worker_lost=True,  # 워커 죽으면 작업 재시도
    # 큐 라우팅 설정 (Backend task_queue_adapter와 일치)
    task_routes={
        "worker.tasks.asr_task.process_asr_task": {"queue": "asr"},
        "worker.tasks.llm_task.process_llm_task": {"queue": "llm_summary"},
        "worker.tasks.ocr_task.process_ocr_task": {"queue": "ocr_tasks"},
        "worker.tasks.search_task.process_search_task": {"queue": "search"},
    },
    task_default_queue="asr",
    task_create_missing_queues=True,

    # Celery Beat 스케줄
    # V6.5: cleanup-stale-resources 제거됨 (리소스 게이트 제거)
    beat_schedule={
        "cleanup-old-temp-files": {
            "task": "worker.tasks.cleanup_task.cleanup_old_temp_files",
            "schedule": 3600.0,  # 1시간마다
        },
    },
)

# Windows에서 동작하도록 설정
if os.name == "nt":
    celery_app.conf.update(
        worker_pool="solo",
    )


# Celery 로거가 설정된 후 포맷 적용
@after_setup_logger.connect
def setup_logger_format(logger, *args, **kwargs):
    """Celery 워커 로거가 설정된 후 포맷을 적용."""
    apply_celery_logging_format(logger)


@after_setup_task_logger.connect
def setup_task_logger_format(logger, *args, **kwargs):
    """Celery task 로거가 설정된 후 포맷을 적용."""
    apply_celery_logging_format(logger)


@worker_process_init.connect
def configure_worker_logging(**kwargs):
    """워커 프로세스가 시작될 때 모든 로거의 포맷을 일관되게 설정."""
    from .logging_config import _log_formatter

    for name in logging.Logger.manager.loggerDict:
        logger = logging.getLogger(name)
        for handler in logger.handlers:
            handler.setFormatter(_log_formatter)

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        handler.setFormatter(_log_formatter)


@worker_process_init.connect
def configure_worker_telemetry(**kwargs):
    """워커 프로세스가 시작될 때 OpenTelemetry 초기화."""
    from .telemetry import setup_worker_telemetry
    setup_worker_telemetry(service_name="asr-worker")


# V6.5: 리소스 게이트 제거됨 - LiteLLM Custom Handler가 직접 라우팅 및 메모리 관리
# 태스크 실패/취소 시 로깅만 수행

@task_failure.connect
def log_task_failure(sender, task_id, exception, traceback, **kwargs):
    """Task 실패 시 로깅."""
    task_name = sender.name
    logging.error(
        f"[Task Failed] {task_name} (task_id={task_id}): {exception}"
    )


@task_revoked.connect
def log_task_revoked(sender, request=None, terminated=None, signum=None, expired=None, **kwargs):
    """Task 강제 종료 시 로깅."""
    task_name = sender.name if sender else "unknown"
    task_id = request.id if request else kwargs.get("task_id", "unknown")
    reason = "terminated" if terminated else ("expired" if expired else "unknown")
    logging.warning(
        f"[Task Revoked] {task_name} (task_id={task_id}), reason={reason}, terminated={terminated}"
    )
