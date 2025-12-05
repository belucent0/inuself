#!/usr/bin/env python
"""celery 큐의 작업을 asr/llm 큐로 이동하는 스크립트."""
import sys
import os
import json

# backend 디렉토리를 Python 경로에 추가
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from app.core.config import get_settings
import redis

settings = get_settings()

# Redis 연결
redis_client = redis.from_url(settings.redis_url, decode_responses=False)

print("=" * 60)
print("celery 큐의 작업을 asr/llm 큐로 이동")
print("=" * 60)

# celery 큐 확인
celery_queue_key = "celery"
queue_length = redis_client.llen(celery_queue_key)

print(f"\ncelery 큐에 {queue_length}개 작업이 있습니다.")

if queue_length == 0:
    print("이동할 작업이 없습니다.")
    sys.exit(0)

moved_count = 0

# 큐의 모든 작업 확인
for i in range(queue_length):
    try:
        # 작업 가져오기 (제거하지 않고 확인)
        task_data = redis_client.lindex(celery_queue_key, i)
        if not task_data:
            continue
            
        # JSON 파싱
        task_json = json.loads(task_data)
        task_name = task_json.get("headers", {}).get("task", "")
        
        # 작업 이름에 따라 큐 결정
        target_queue = None
        if "process_asr_task" in task_name:
            target_queue = "asr"
        elif "process_llm_task" in task_name:
            target_queue = "llm"
        
        if target_queue:
            # 작업을 새 큐로 이동
            redis_client.rpush(target_queue, task_data)
            redis_client.lrem(celery_queue_key, 1, task_data)
            moved_count += 1
            print(f"  [OK] 작업 이동: {task_name} -> {target_queue} 큐")
        else:
            print(f"  [SKIP] 알 수 없는 작업: {task_name}")
            
    except Exception as e:
        print(f"  [ERROR] 작업 처리 실패: {e}")

print(f"\n총 {moved_count}개 작업을 이동했습니다.")
print("=" * 60)

