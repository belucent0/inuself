#!/usr/bin/env python
"""Celery 큐 상태 확인 스크립트."""
import sys
import os

# backend 디렉토리를 Python 경로에 추가
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from app.core.config import get_settings
from celery import Celery
from celery.result import AsyncResult

settings = get_settings()

# Celery 앱 생성
celery_app = Celery(
    "torch_asr",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

# 워커 상태 확인
print("=" * 60)
print("Celery 워커 상태 확인")
print("=" * 60)

inspect = celery_app.control.inspect()

# 활성 작업 확인
print("\n[활성 작업 (Active Tasks)]")
active = inspect.active()
if active:
    for worker_name, tasks in active.items():
        print(f"\n워커: {worker_name}")
        if tasks:
            for task in tasks:
                print(f"  - Task ID: {task['id']}")
                print(f"    Name: {task['name']}")
                print(f"    Args: {task.get('args', [])}")
                print(f"    Started: {task.get('time_start', 'N/A')}")
        else:
            print("  (활성 작업 없음)")
else:
    print("  (활성 작업 없음)")

# 예약된 작업 확인
print("\n[예약된 작업 (Reserved Tasks)]")
reserved = inspect.reserved()
if reserved:
    for worker_name, tasks in reserved.items():
        print(f"\n워커: {worker_name}")
        if tasks:
            for task in tasks:
                print(f"  - Task ID: {task['id']}")
                print(f"    Name: {task['name']}")
                print(f"    Args: {task.get('args', [])}")
        else:
            print("  (예약된 작업 없음)")
else:
    print("  (예약된 작업 없음)")

# 스케줄된 작업 확인
print("\n[스케줄된 작업 (Scheduled Tasks)]")
scheduled = inspect.scheduled()
if scheduled:
    for worker_name, tasks in scheduled.items():
        print(f"\n워커: {worker_name}")
        if tasks:
            for task in tasks:
                print(f"  - Task ID: {task['id']}")
                print(f"    Name: {task['name']}")
                print(f"    ETA: {task.get('eta', 'N/A')}")
        else:
            print("  (스케줄된 작업 없음)")
else:
    print("  (스케줄된 작업 없음)")

# 등록된 워커 확인
print("\n[등록된 워커 (Registered Workers)]")
registered = inspect.registered()
if registered:
    for worker_name, tasks in registered.items():
        print(f"\n워커: {worker_name}")
        print(f"  등록된 태스크 수: {len(tasks)}")
        if 'process_asr_task' in tasks:
            print("  ✓ process_asr_task 등록됨")
        if 'process_llm_task' in tasks:
            print("  ✓ process_llm_task 등록됨")
else:
    print("  (등록된 워커 없음)")

# 통계 정보
print("\n[통계 정보]")
stats = inspect.stats()
if stats:
    for worker_name, stat in stats.items():
        print(f"\n워커: {worker_name}")
        print(f"  Pool: {stat.get('pool', {}).get('implementation', 'N/A')}")
        print(f"  Concurrency: {stat.get('pool', {}).get('max-concurrency', 'N/A')}")
        print(f"  Total tasks: {stat.get('total', {}).get('tasks.succeeded', 0)}")
else:
    print("  (통계 정보 없음)")

print("\n" + "=" * 60)


