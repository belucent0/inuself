"""Celery 큐 작업 취소 및 확인 유틸리티."""
from ..core.config import get_settings
from ..core.logging import logger
from .celery_app import celery_app


def is_celery_task_in_queue(*, content_id: int, task_name: str = "process_llm_task") -> bool:
    """
    해당 content_id의 Celery 작업이 큐에 이미 있는지 확인.
    
    Args:
        content_id: 확인할 콘텐츠 ID
        task_name: 확인할 작업 이름 ("process_llm_task" 또는 "process_asr_task")
        
    Returns:
        작업이 큐에 있으면 True, 없으면 False
    """
    settings = get_settings()
    if settings.task_queue_type.lower() != "celery":
        return False
    
    try:
        inspector = celery_app.control.inspect()
        
        # 활성 작업 확인 (현재 실행 중인 작업)
        active = inspector.active()
        if active:
            for worker_name, tasks in active.items():
                for task in tasks:
                    if task.get("name") == task_name:
                        task_kwargs = task.get("kwargs", {})
                        task_args = task.get("args", [])
                        task_content_id = task_kwargs.get("content_id") or (task_args[0] if task_args else None)
                        if task_content_id == content_id:
                            return True
        
        # 예약된 작업 확인 (큐에 대기 중인 작업)
        reserved = inspector.reserved()
        if reserved:
            for worker_name, tasks in reserved.items():
                for task in tasks:
                    if task.get("name") == task_name:
                        task_kwargs = task.get("kwargs", {})
                        task_args = task.get("args", [])
                        task_content_id = task_kwargs.get("content_id") or (task_args[0] if task_args else None)
                        if task_content_id == content_id:
                            return True
        
        # 스케줄된 작업 확인 (ETA로 예약된 작업)
        scheduled = inspector.scheduled()
        if scheduled:
            for worker_name, tasks in scheduled.items():
                for task in tasks:
                    request = task.get("request", {})
                    if request.get("task") == task_name:
                        task_kwargs = request.get("kwargs", {})
                        task_args = request.get("args", [])
                        task_content_id = task_kwargs.get("content_id") or (task_args[0] if task_args else None)
                        if task_content_id == content_id:
                            return True
        
        return False
    except Exception as e:
        logger.warning("Failed to check Celery task in queue: %s", e)
        return False


def cancel_celery_tasks_by_content_ids(content_ids: list[int]) -> int:
    """
    주어진 content_id와 매칭되는 Celery 작업을 취소합니다.
    
    Args:
        content_ids: 취소할 콘텐츠 ID 리스트
        
    Returns:
        취소된 작업 수
    """
    if not content_ids:
        return 0
    
    settings = get_settings()
    if settings.task_queue_type.lower() != "celery":
        # Celery를 사용하지 않으면 취소할 작업이 없음
        return 0
    
    cancelled_count = 0
    content_ids_set = set(content_ids)
    
    try:
        # Celery Inspector를 사용하여 활성/예약/스케줄된 작업 확인
        inspector = celery_app.control.inspect()
        
        # 활성 작업 확인 (현재 실행 중인 작업)
        active = inspector.active()
        if active:
            for worker_name, tasks in active.items():
                for task in tasks:
                    task_name = task.get("name", "")
                    task_kwargs = task.get("kwargs", {})
                    task_args = task.get("args", [])
                    
                    # ASR 또는 LLM 작업인지 확인
                    if task_name in ["process_asr_task", "process_llm_task"]:
                        # content_id 추출
                        task_content_id = None
                        if task_name == "process_llm_task":
                            task_content_id = task_kwargs.get("content_id") or (task_args[0] if task_args else None)
                        elif task_name == "process_asr_task":
                            task_content_id = task_kwargs.get("content_id") or (task_args[0] if task_args else None)
                        
                        if task_content_id and task_content_id in content_ids_set:
                            task_id = task.get("id")
                            try:
                                celery_app.control.revoke(task_id, terminate=True)
                                cancelled_count += 1
                                logger.info(f"[Celery] 활성 작업 취소: content_id={task_content_id}, task_id={task_id}")
                                logger.info("Cancelled active Celery task: content_id=%s, task_id=%s", task_content_id, task_id)
                            except Exception as e:
                                logger.warning("Failed to cancel active Celery task %s: %s", task_id, e)
        
        # 예약된 작업 확인 (큐에 대기 중인 작업)
        reserved = inspector.reserved()
        if reserved:
            for worker_name, tasks in reserved.items():
                for task in tasks:
                    task_name = task.get("name", "")
                    task_kwargs = task.get("kwargs", {})
                    task_args = task.get("args", [])
                    
                    if task_name in ["process_asr_task", "process_llm_task"]:
                        task_content_id = None
                        if task_name == "process_llm_task":
                            task_content_id = task_kwargs.get("content_id") or (task_args[0] if task_args else None)
                        elif task_name == "process_asr_task":
                            task_content_id = task_kwargs.get("content_id") or (task_args[0] if task_args else None)
                        
                        if task_content_id and task_content_id in content_ids_set:
                            task_id = task.get("id")
                            try:
                                celery_app.control.revoke(task_id, terminate=True)
                                cancelled_count += 1
                                logger.info(f"[Celery] 예약된 작업 취소: content_id={task_content_id}, task_id={task_id}")
                                logger.info("Cancelled reserved Celery task: content_id=%s, task_id=%s", task_content_id, task_id)
                            except Exception as e:
                                logger.warning("Failed to cancel reserved Celery task %s: %s", task_id, e)
        
        # 스케줄된 작업 확인 (ETA로 예약된 작업)
        scheduled = inspector.scheduled()
        if scheduled:
            for worker_name, tasks in scheduled.items():
                for task in tasks:
                    request = task.get("request", {})
                    task_name = request.get("task", "")
                    task_kwargs = request.get("kwargs", {})
                    task_args = request.get("args", [])
                    
                    if task_name in ["process_asr_task", "process_llm_task"]:
                        task_content_id = None
                        if task_name == "process_llm_task":
                            task_content_id = task_kwargs.get("content_id") or (task_args[0] if task_args else None)
                        elif task_name == "process_asr_task":
                            task_content_id = task_kwargs.get("content_id") or (task_args[0] if task_args else None)
                        
                        if task_content_id and task_content_id in content_ids_set:
                            task_id = request.get("id")
                            try:
                                celery_app.control.revoke(task_id, terminate=True)
                                cancelled_count += 1
                                logger.info(f"[Celery] 스케줄된 작업 취소: content_id={task_content_id}, task_id={task_id}")
                                logger.info("Cancelled scheduled Celery task: content_id=%s, task_id=%s", task_content_id, task_id)
                            except Exception as e:
                                logger.warning("Failed to cancel scheduled Celery task %s: %s", task_id, e)
        
        if cancelled_count > 0:
            logger.info(f"[Celery] 총 {cancelled_count}개의 Celery 작업이 취소되었습니다.")
        else:
            logger.info("[Celery] 취소할 Celery 작업이 없습니다.")
            
    except Exception as e:
        logger.error("Failed to cancel Celery tasks: %s", e)
        logger.error(f"[Celery] 작업 취소 중 오류 발생: {e}")
    
    return cancelled_count
