"""Celery 큐 작업 취소 및 확인 유틸리티."""
from ..core.config import get_settings
from ..core.logging import logger
from .celery_app import celery_app


def is_celery_task_in_queue(*, file_id: int, task_name: str = "process_llm_task") -> bool:
    """
    해당 file_id의 Celery 작업이 큐에 이미 있는지 확인.
    
    Args:
        file_id: 확인할 파일 ID
        task_name: 확인할 작업 이름 ("process_llm_task", "process_asr_task", 또는 "process_ocr_task")
        
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
                        # file_id 확인
                        task_file_id = task_kwargs.get("file_id") or (task_args[0] if task_args else None)
                        if task_file_id == file_id:
                            return True
        
        # 예약된 작업 확인 (큐에 대기 중인 작업)
        reserved = inspector.reserved()
        if reserved:
            for worker_name, tasks in reserved.items():
                for task in tasks:
                    if task.get("name") == task_name:
                        task_kwargs = task.get("kwargs", {})
                        task_args = task.get("args", [])
                        # file_id 확인
                        task_file_id = task_kwargs.get("file_id") or (task_args[0] if task_args else None)
                        if task_file_id == file_id:
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
                        # file_id 확인
                        task_file_id = task_kwargs.get("file_id") or (task_args[0] if task_args else None)
                        if task_file_id == file_id:
                            return True
        
        return False
    except Exception as e:
        logger.warning("Failed to check Celery task in queue: %s", e)
        return False


def cancel_celery_tasks_by_content_ids(file_ids: list[int]) -> int:
    """
    주어진 file_id와 매칭되는 Celery 작업을 취소합니다.
    
    Args:
        file_ids: 취소할 파일 ID 리스트 (함수명은 하위 호환성을 위해 유지)
        
    Returns:
        취소된 작업 수
    """
    if not file_ids:
        return 0
    
    settings = get_settings()
    if settings.task_queue_type.lower() != "celery":
        # Celery를 사용하지 않으면 취소할 작업이 없음
        return 0
    
    cancelled_count = 0
    file_ids_set = set(file_ids)
    
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
                    
                    # ASR, LLM, 또는 OCR 작업인지 확인
                    if task_name in ["process_asr_task", "process_llm_task", "process_ocr_task"]:
                        # file_id 추출
                        task_file_id = task_kwargs.get("file_id") or (task_args[0] if task_args else None)
                        
                        if task_file_id and task_file_id in file_ids_set:
                            task_id = task.get("id")
                            try:
                                celery_app.control.revoke(task_id, terminate=True)
                                cancelled_count += 1
                                logger.info(f"[Celery] 활성 작업 취소: file_id={task_file_id}, task_id={task_id}")
                                logger.info("Cancelled active Celery task: file_id=%s, task_id=%s", task_file_id, task_id)
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
                    
                    if task_name in ["process_asr_task", "process_llm_task", "process_ocr_task"]:
                        # file_id 추출
                        task_file_id = task_kwargs.get("file_id") or (task_args[0] if task_args else None)
                        
                        if task_file_id and task_file_id in file_ids_set:
                            task_id = task.get("id")
                            try:
                                celery_app.control.revoke(task_id, terminate=True)
                                cancelled_count += 1
                                logger.info(f"[Celery] 예약된 작업 취소: file_id={task_file_id}, task_id={task_id}")
                                logger.info("Cancelled reserved Celery task: file_id=%s, task_id=%s", task_file_id, task_id)
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
                    
                    if task_name in ["process_asr_task", "process_llm_task", "process_ocr_task"]:
                        # file_id 추출
                        task_file_id = task_kwargs.get("file_id") or (task_args[0] if task_args else None)
                        
                        if task_file_id and task_file_id in file_ids_set:
                            task_id = request.get("id")
                            try:
                                celery_app.control.revoke(task_id, terminate=True)
                                cancelled_count += 1
                                logger.info(f"[Celery] 스케줄된 작업 취소: file_id={task_file_id}, task_id={task_id}")
                                logger.info("Cancelled scheduled Celery task: file_id=%s, task_id=%s", task_file_id, task_id)
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
