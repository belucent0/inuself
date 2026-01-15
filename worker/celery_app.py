"""Celery 애플리케이션 설정."""
import os
import logging

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
    # 큐 라우팅 설정
    task_routes={
        "worker.tasks.asr_task.process_asr_task": {"queue": "asr"},
        "worker.tasks.llm_task.process_llm_task": {"queue": "llm"},
        "worker.tasks.ocr_task.process_ocr_task": {"queue": "ocr"},
    },
    task_default_queue="asr",
    task_create_missing_queues=True,

    # Celery Beat 스케줄
    beat_schedule={
        "cleanup-stale-resources": {
            "task": "worker.tasks.cleanup_task.cleanup_stale_resources",
            "schedule": 300.0,  # 5분마다
        },
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


def _force_release_resource(task_id: str) -> None:
    """특정 task_id가 점유한 리소스 락을 강제 해제합니다."""
    import httpx
    import json

    settings = get_settings()
    litellm_url = os.getenv("LITELLM_BASE_URL", "http://litellm:4000")

    # 모든 리소스 타입/테스크 타입 조합 확인
    combinations = [
        ("gpu", "asr"),
        ("gpu", "ocr"),
        ("gpu", "llm"),
        ("gpu", "diarization"),
        ("npu", "asr"),
        ("npu", "ocr"),
        ("npu", "llm"),
    ]

    for resource_type, task_type in combinations:
        gate_key = f"resource:gate:{resource_type}:{task_type}"

        try:
            # 해당 task_id가 락을 가지고 있는지 확인
            response = httpx.get(
                f"{litellm_url}/resource/status",
                timeout=5.0
            )
            response.raise_for_status()
            status_data = response.json()

            # gate_key 상태 확인
            key_status = status_data.get("status", {}).get(f"{resource_type}-{task_type}", {})
            if key_status.get("locked"):
                lock_data = key_status.get("data", "")
                if isinstance(lock_data, str):
                    try:
                        lock_info = json.loads(lock_data)
                        if lock_info.get("task_id") == task_id:
                            # 이 task가 소유한 락 발견 - 강제 해제
                            force_response = httpx.post(
                                f"{litellm_url}/resource/force-release",
                                json={
                                    "resource_type": resource_type,
                                    "task_type": task_type,
                                },
                                timeout=5.0
                            )
                            force_response.raise_for_status()
                            result = force_response.json()
                            logging.info(
                                f"[Resource Cleanup] Force released lock for task {task_id}: "
                                f"{resource_type}/{task_type} (was owner: {result.get('previous_owner')})"
                            )
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            # force-release 실패 시 로그만 남기고 계속 진행
            logging.warning(f"[Resource Cleanup] Failed to check/release {gate_key}: {e}")


@task_failure.connect
def cleanup_on_task_failure(sender, task_id, exception, traceback, **kwargs):
    """Task 실패 시 리소스 정리."""
    task_name = sender.name
    logging.warning(
        f"[Resource Cleanup] Task failed: {task_name} (task_id={task_id}), cleaning up resources..."
    )
    _force_release_resource(task_id)


@task_revoked.connect
def cleanup_on_task_revoked(sender, task_id, reason, signum, terminated, **kwargs):
    """Task 강제 종료 시 리소스 정리."""
    task_name = sender.name
    logging.warning(
        f"[Resource Cleanup] Task revoked: {task_name} (task_id={task_id}), cleaning up resources..."
    )
    _force_release_resource(task_id)
