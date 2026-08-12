"""Celery application for queued AI agent runs."""

from celery import Celery

from .core.config import get_settings


settings = get_settings()

celery_app = Celery(
    "agent_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.agent_task"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=1800,
    task_soft_time_limit=1740,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_routes={
        "app.tasks.agent_task.process_agent_message": {"queue": "agent"},
    },
    task_default_queue="agent",
    task_create_missing_queues=True,
)
