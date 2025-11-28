#!/usr/bin/env python
"""Celery 작업을 정리하는 스크립트."""
import sys
from pathlib import Path

# backend 디렉터리를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from app.worker.celery_app import celery_app
from celery.result import AsyncResult
from celery import states


def purge_all_tasks():
    """모든 대기 중인 Celery 작업을 삭제합니다."""
    print("=" * 60)
    print("Celery 작업 전체 삭제")
    print("=" * 60)
    
    try:
        # Celery purge 명령 사용
        purged = celery_app.control.purge()
        print(f"\n삭제된 작업 수: {purged}")
        print("모든 대기 중인 작업이 삭제되었습니다.")
    except Exception as e:
        print(f"작업 삭제 실패: {e}")
        # Redis에서 직접 삭제 시도
        try:
            from app.core.redis import get_redis_connection
            redis = get_redis_connection()
            
            # Celery 큐 키 패턴
            queue_keys = list(redis.scan_iter(match="celery"))
            deleted = 0
            for key in queue_keys:
                try:
                    redis.delete(key)
                    deleted += 1
                except Exception:
                    pass
            print(f"Redis에서 직접 삭제: {deleted}개 키 삭제")
        except Exception as e2:
            print(f"Redis 직접 삭제도 실패: {e2}")


def cleanup_celery_tasks():
    """Celery의 실패한 작업들을 정리합니다."""
    print("=" * 60)
    print("Celery 작업 정리 스크립트")
    print("=" * 60)
    
    # Celery Inspector를 사용하여 작업 상태 확인
    inspector = celery_app.control.inspect()
    
    # 활성 작업 확인
    active = inspector.active()
    scheduled = inspector.scheduled()
    reserved = inspector.reserved()
    
    print("\n[현재 Celery 작업 상태]")
    
    active_count = 0
    scheduled_count = 0
    reserved_count = 0
    
    if active:
        for worker_name, tasks in active.items():
            active_count += len(tasks)
            print(f"활성 작업 ({worker_name}): {len(tasks)}개")
            for task in tasks[:5]:  # 최대 5개만 표시
                print(f"  - {task.get('name', 'unknown')}: {task.get('id', 'unknown')}")
    
    if scheduled:
        for worker_name, tasks in scheduled.items():
            scheduled_count += len(tasks)
            print(f"예약된 작업 ({worker_name}): {len(tasks)}개")
            for task in tasks[:5]:
                task_id = task.get('request', {}).get('id', 'unknown')
                print(f"  - {task.get('request', {}).get('task', 'unknown')}: {task_id}")
    
    if reserved:
        for worker_name, tasks in reserved.items():
            reserved_count += len(tasks)
            print(f"예약된 작업 (reserved) ({worker_name}): {len(tasks)}개")
    
    print(f"\n총 활성 작업: {active_count}개")
    print(f"총 예약된 작업: {scheduled_count}개")
    print(f"총 예약된 작업 (reserved): {reserved_count}개")
    
    # 전체 작업 삭제 옵션
    if active_count > 0 or scheduled_count > 0 or reserved_count > 0:
        print("\n[작업 정리 옵션]")
        print("1. 모든 대기 중인 작업 삭제 (purge)")
        print("2. 취소")
        choice = input("선택 (1/2 또는 Enter로 취소): ").strip()
        
        if choice == "1":
            purge_all_tasks()
        else:
            print("취소되었습니다.")
    
    print("\n" + "=" * 60)
    print("정리 완료")
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Celery 작업 정리 스크립트")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="모든 대기 중인 작업을 즉시 삭제"
    )
    
    args = parser.parse_args()
    
    if args.purge:
        purge_all_tasks()
    else:
        cleanup_celery_tasks()

