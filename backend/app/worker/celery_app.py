"""Celery 애플리케이션 설정."""
import os
import logging
from celery import Celery
from celery.signals import worker_process_init, after_setup_logger, after_setup_task_logger
from ..core.config import get_settings

settings = get_settings()

# Celery 앱 생성
celery_app = Celery(
    "torch_asr",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.worker.celery_tasks"],
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
    worker_max_tasks_per_child=100,  # 100개 작업 후 자동 재시작 (리소스 누수 방지)
    result_expires=3600,  # 결과 1시간 보관
    task_acks_late=True,  # 작업 완료 후 ack
    task_reject_on_worker_lost=True,  # 워커 죽으면 작업 재시도
    # 큐 라우팅 설정 (태스크 이름을 정확히 지정)
    task_routes={
        "process_asr_task": {"queue": "asr"},
        "process_llm_task": {"queue": "llm"},
        "process_ocr_task": {"queue": "ocr"},
    },
    # 기본 큐 비활성화 (명시적 큐만 사용)
    task_default_queue="asr",  # 기본값을 asr로 설정 (하지만 명시적 라우팅 사용)
    # 큐 자동 생성 활성화
    task_create_missing_queues=True,
)

# Windows에서 동작하도록 설정
if os.name == "nt":
    # Windows에서는 eventlet 또는 gevent 사용 권장
    celery_app.conf.update(
        worker_pool="solo",  # Windows에서 안전한 solo pool
    )


# 타임스탬프를 제거한 포맷 (Loguru와 일관성 유지)
_log_formatter = logging.Formatter(
    "%(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
)


def _apply_logging_format(logger: logging.Logger) -> None:
    """로거의 모든 핸들러에 포맷을 적용."""
    for handler in logger.handlers:
        handler.setFormatter(_log_formatter)


# Celery 로거가 설정된 후 포맷 적용
@after_setup_logger.connect
def setup_logger_format(logger, *args, **kwargs):
    """Celery 워커 로거가 설정된 후 포맷을 적용."""
    _apply_logging_format(logger)


# Celery task 로거가 설정된 후 포맷 적용
@after_setup_task_logger.connect
def setup_task_logger_format(logger, *args, **kwargs):
    """Celery task 로거가 설정된 후 포맷을 적용."""
    _apply_logging_format(logger)


# Celery 워커 프로세스가 시작될 때 모든 로거 포맷 설정
@worker_process_init.connect
def configure_worker_logging(**kwargs):
    """워커 프로세스가 시작될 때 모든 로거의 포맷을 일관되게 설정."""
    # 모든 기존 로거에 포맷 적용
    for name in logging.Logger.manager.loggerDict:
        logger = logging.getLogger(name)
        _apply_logging_format(logger)
    
    # 루트 로거에도 포맷 적용
    root_logger = logging.getLogger()
    _apply_logging_format(root_logger)









