"""Celery 애플리케이션 설정."""
import os
from celery import Celery
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
)

# Windows에서 동작하도록 설정
if os.name == "nt":
    # Windows에서는 eventlet 또는 gevent 사용 권장
    celery_app.conf.update(
        worker_pool="solo",  # Windows에서 안전한 solo pool
    )





