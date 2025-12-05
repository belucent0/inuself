#!/usr/bin/env python
"""특정 Celery 작업 ID의 상태 확인 스크립트."""
import sys
import os

# backend 디렉토리를 Python 경로에 추가
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from app.core.config import get_settings
from celery.result import AsyncResult
from app.worker.celery_app import celery_app

if len(sys.argv) < 2:
    print("사용법: python check_celery_task.py <task_id>")
    print("예시: python check_celery_task.py 1f4b9866-703a-478d-8117-18f4466a5a90")
    sys.exit(1)

task_id = sys.argv[1]

print("=" * 60)
print(f"Celery 작업 상태 확인: {task_id}")
print("=" * 60)

result = AsyncResult(task_id, app=celery_app)

print(f"\n작업 ID: {task_id}")
print(f"상태: {result.state}")
print(f"성공: {result.successful()}")
print(f"실패: {result.failed()}")
print(f"준비: {result.ready()}")

if result.info:
    print(f"\n정보:")
    if isinstance(result.info, dict):
        for key, value in result.info.items():
            print(f"  {key}: {value}")
    else:
        print(f"  {result.info}")

if result.traceback:
    print(f"\n에러 추적:")
    print(result.traceback)

print("\n" + "=" * 60)

