import logging
from functools import lru_cache
from rq import Queue

from ..core.redis import get_redis_connection
from .processor import process_transcription_job

QUEUE_NAME = "asr_tasks"

logger = logging.getLogger(__name__)


@lru_cache
def _get_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=get_redis_connection())


def enqueue_transcription_job(
    *,
    content_id: int,
    storage_key: str,
    original_filename: str,
    model_size: str,
    processing_mode: str,
    num_asr_chunks: int,
) -> None:
    """작업을 큐에 등록."""
    queue = _get_queue()
    job = queue.enqueue(
        process_transcription_job,
        content_id=content_id,
        storage_key=storage_key,
        original_filename=original_filename,
        model_size=model_size,
        processing_mode=processing_mode,
        num_asr_chunks=num_asr_chunks,
    )
    print(f"[Queue] 작업 등록됨: content_id={content_id}, job_id={job.id}, 큐 크기={len(queue)}")
    logger.info("Job enqueued: content_id=%s, job_id=%s, queue_size=%s", content_id, job.id, len(queue))


def cancel_jobs_by_content_ids(content_ids: list[int]) -> int:
    """content_id 리스트에 해당하는 큐 작업들을 취소/삭제."""
    if not content_ids:
        return 0
    
    queue = _get_queue()
    cancelled_count = 0
    
    # 큐의 모든 작업 조회
    job_ids = queue.get_job_ids()
    print(f"[Queue] 큐에서 {len(job_ids)}개의 작업을 확인 중... (삭제 대상: {len(content_ids)}개)")
    
    for job_id in job_ids:
        try:
            job = queue.fetch_job(job_id)
            if job is None:
                continue
            
            # 작업의 인자에서 content_id 추출
            # RQ는 키워드 인자를 job.kwargs에 저장
            job_kwargs = getattr(job, 'kwargs', {})
            job_content_id = job_kwargs.get('content_id')
            
            if job_content_id and job_content_id in content_ids:
                # 작업 취소
                try:
                    job.cancel()
                except Exception:
                    pass  # 이미 실행 중이거나 완료된 작업은 취소 불가
                
                # 작업 삭제
                job.delete()
                cancelled_count += 1
                print(f"[Queue] 작업 취소/삭제됨: content_id={job_content_id}, job_id={job_id}")
                logger.info("Job cancelled: content_id=%s, job_id=%s", job_content_id, job_id)
        except Exception as e:
            logger.warning("Failed to cancel job %s: %s", job_id, e)
            continue
    
    print(f"[Queue] 총 {cancelled_count}개의 작업이 큐에서 삭제되었습니다.")
    return cancelled_count

