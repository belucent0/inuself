#!/usr/bin/env python
"""큐의 stale 작업을 수동으로 정리하는 스크립트."""
import sys
from pathlib import Path

# backend 디렉터리를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from app.worker.cleanup import cleanup_all_queues, get_queue_stats


def main():
    """메인 함수."""
    print("=" * 60)
    print("RQ 큐 정리 스크립트")
    print("=" * 60)
    
    # 현재 상태 출력
    print("\n[현재 큐 상태]")
    for queue_name in ["asr_tasks", "llm_tasks"]:
        stats = get_queue_stats(queue_name)
        print(f"{queue_name}:")
        print(f"  - 대기 중: {stats['queued']}개")
        print(f"  - 처리 중: {stats['started']}개")
        print(f"  - 실패: {stats['failed']}개")
    
    # 정리 실행
    print("\n[Stale 작업 정리 시작]")
    cleanup_all_queues(requeue=True)
    
    # 정리 후 상태 출력
    print("\n[정리 후 큐 상태]")
    for queue_name in ["asr_tasks", "llm_tasks"]:
        stats = get_queue_stats(queue_name)
        print(f"{queue_name}:")
        print(f"  - 대기 중: {stats['queued']}개")
        print(f"  - 처리 중: {stats['started']}개")
        print(f"  - 실패: {stats['failed']}개")
    
    print("\n" + "=" * 60)
    print("정리 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()


