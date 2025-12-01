"""큐 상태 디버깅 유틸리티."""
from rq import Queue
from rq.job import Job
from rq.registry import (
    StartedJobRegistry,
    FinishedJobRegistry,
    FailedJobRegistry,
    DeferredJobRegistry,
    ScheduledJobRegistry,
)

from ..core.redis import get_redis_connection


def debug_queue_state(queue_name: str = "asr_tasks") -> None:
    """큐의 모든 레지스트리 상태를 출력합니다."""
    redis = get_redis_connection()
    queue = Queue(queue_name, connection=redis)
    
    print(f"\n{'='*60}")
    print(f"큐 디버그 정보: {queue_name}")
    print(f"{'='*60}\n")
    
    # 1. 큐에 대기 중인 작업
    print(f"[Queue] 대기 중인 작업: {len(queue)}개")
    job_ids = queue.get_job_ids()
    for i, job_id in enumerate(job_ids[:5], 1):
        job = queue.fetch_job(job_id)
        if job:
            content_id = job.kwargs.get('content_id', 'N/A')
            print(f"  {i}. Job ID: {job_id[:16]}..., content_id: {content_id}, status: {job.get_status()}")
    if len(job_ids) > 5:
        print(f"  ... 그 외 {len(job_ids) - 5}개")
    print()
    
    # 2. StartedJobRegistry (실행 중)
    started_registry = StartedJobRegistry(queue=queue)
    started_job_ids = started_registry.get_job_ids()
    print(f"[Started] 실행 중인 작업: {len(started_job_ids)}개")
    for i, job_id in enumerate(started_job_ids[:5], 1):
        job = Job.fetch(job_id, connection=redis)
        if job:
            content_id = job.kwargs.get('content_id', 'N/A')
            print(f"  {i}. Job ID: {job_id[:16]}..., content_id: {content_id}, status: {job.get_status()}")
    if len(started_job_ids) > 5:
        print(f"  ... 그 외 {len(started_job_ids) - 5}개")
    print()
    
    # 3. FinishedJobRegistry (완료)
    finished_registry = FinishedJobRegistry(queue=queue)
    finished_job_ids = finished_registry.get_job_ids()
    print(f"[Finished] 완료된 작업: {len(finished_job_ids)}개")
    for i, job_id in enumerate(finished_job_ids[:3], 1):
        job = Job.fetch(job_id, connection=redis)
        if job:
            content_id = job.kwargs.get('content_id', 'N/A')
            print(f"  {i}. Job ID: {job_id[:16]}..., content_id: {content_id}")
    if len(finished_job_ids) > 3:
        print(f"  ... 그 외 {len(finished_job_ids) - 3}개")
    print()
    
    # 4. FailedJobRegistry (실패)
    failed_registry = FailedJobRegistry(queue=queue)
    failed_job_ids = failed_registry.get_job_ids()
    print(f"[Failed] 실패한 작업: {len(failed_job_ids)}개")
    for i, job_id in enumerate(failed_job_ids[:3], 1):
        try:
            job = Job.fetch(job_id, connection=redis)
            if job:
                content_id = job.kwargs.get('content_id', 'N/A')
                exc_info = job.exc_info[:100] if job.exc_info else 'N/A'
                print(f"  {i}. Job ID: {job_id[:16]}..., content_id: {content_id}")
                print(f"      Error: {exc_info}...")
        except Exception as e:
            print(f"  {i}. Job ID: {job_id[:16]}... (fetch 실패: {e})")
    if len(failed_job_ids) > 3:
        print(f"  ... 그 외 {len(failed_job_ids) - 3}개")
    print()
    
    # 5. DeferredJobRegistry (의존성)
    deferred_registry = DeferredJobRegistry(queue=queue)
    deferred_job_ids = deferred_registry.get_job_ids()
    print(f"[Deferred] 지연된 작업: {len(deferred_job_ids)}개")
    print()
    
    # 6. ScheduledJobRegistry (예약)
    scheduled_registry = ScheduledJobRegistry(queue=queue)
    scheduled_job_ids = scheduled_registry.get_job_ids()
    print(f"[Scheduled] 예약된 작업: {len(scheduled_job_ids)}개")
    print()
    
    # 특정 content_id 검색
    print(f"{'='*60}")
    print("특정 content_id 검색 (114):")
    print(f"{'='*60}\n")
    
    all_registries = [
        ("Queue", job_ids),
        ("Started", started_job_ids),
        ("Finished", finished_job_ids),
        ("Failed", failed_job_ids),
        ("Deferred", deferred_job_ids),
        ("Scheduled", scheduled_job_ids),
    ]
    
    found = False
    for registry_name, registry_job_ids in all_registries:
        for job_id in registry_job_ids:
            try:
                job = Job.fetch(job_id, connection=redis)
                if job and job.kwargs.get('content_id') == 114:
                    print(f"[{registry_name}] Job ID: {job_id}")
                    print(f"  Status: {job.get_status()}")
                    print(f"  Created: {job.created_at}")
                    print(f"  Enqueued: {job.enqueued_at}")
                    print(f"  Started: {job.started_at}")
                    print(f"  Ended: {job.ended_at}")
                    if job.exc_info:
                        print(f"  Error: {job.exc_info[:200]}...")
                    print()
                    found = True
            except Exception:
                continue
    
    if not found:
        print("content_id=114인 작업을 찾을 수 없습니다.\n")
    
    print(f"{'='*60}\n")


if __name__ == "__main__":
    debug_queue_state()







