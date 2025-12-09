"""Redis 기반 분산 락 유틸리티."""
from contextlib import contextmanager
from typing import Optional

from celery.exceptions import Retry
from redis.exceptions import ConnectionError, LockError

from ..core.logging import logger
from ..core.redis import get_redis_connection


class LockContextManager:
    """Redis 락을 관리하는 컨텍스트 매니저 클래스."""
    
    def __init__(
        self,
        lock_key: str,
        timeout: float = 1800.0,
        blocking_timeout: float = 0.0,
    ):
        self.lock_key = lock_key
        self.timeout = timeout
        self.blocking_timeout = blocking_timeout
        self.redis_client = None
        self.lock = None
        self.acquired = False
    
    def __enter__(self):
        """락 획득 시도."""
        self.redis_client = _get_redis_client()
        
        if self.redis_client is None:
            # Redis가 없으면 락 없이 진행 (하위 호환성)
            logger.warning("Redis가 없어 락 없이 진행합니다: {}", self.lock_key)
            self.acquired = True
            return self.acquired
        
        try:
            # Redis Lock 생성
            self.lock = self.redis_client.lock(
                self.lock_key,
                timeout=self.timeout,
                blocking_timeout=self.blocking_timeout,
                thread_local=False,  # 멀티스레드 환경에서도 동작하도록
            )
            
            # 락 획득 시도
            self.acquired = self.lock.acquire(blocking=False)
            
            if self.acquired:
                logger.info("락 획득 성공: {}", self.lock_key)
            else:
                # 락 획득 실패 시 TTL 정보 조회
                ttl_seconds = None
                try:
                    ttl_seconds = self.redis_client.ttl(self.lock_key)
                    if ttl_seconds > 0:
                        # TTL을 읽기 쉬운 형식으로 변환
                        minutes = ttl_seconds // 60
                        seconds = ttl_seconds % 60
                        if minutes > 0:
                            ttl_str = f"{minutes}분 {seconds}초"
                        else:
                            ttl_str = f"{seconds}초"
                        logger.warning(
                            "락 획득 실패 (다른 워커가 처리 중): {}, 락 해제 예상 시간: {} (TTL: {}초)",
                            self.lock_key,
                            ttl_str,
                            ttl_seconds
                        )
                    elif ttl_seconds == -1:
                        # TTL이 없는 경우 (이론적으로는 발생하지 않아야 함)
                        logger.warning(
                            "락 획득 실패 (다른 워커가 처리 중): {}, 락 TTL 정보 없음",
                            self.lock_key
                        )
                    else:
                        # ttl_seconds == -2: 키가 존재하지 않음
                        logger.warning(
                            "락 획득 실패: {}, 락이 존재하지 않음 (이미 해제됨)",
                            self.lock_key
                        )
                except Exception as e:
                    logger.warning(
                        "락 획득 실패 (다른 워커가 처리 중): {}, TTL 조회 실패: {}",
                        self.lock_key,
                        e
                    )
            
            return self.acquired
            
        except (LockError, ConnectionError, Exception) as e:
            logger.error("락 획득 중 오류 발생: {}, error={}", self.lock_key, e)
            self.acquired = False
            return self.acquired
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """락 해제."""
        if self.lock and self.acquired:
            try:
                self.lock.release()
                logger.info("락 해제 완료: {}", self.lock_key)
            except Exception as e:
                logger.warning("락 해제 중 오류 발생: {}, error={}", self.lock_key, e)
        
        # 예외를 그대로 전파 (False 반환)
        return False


def _get_redis_client() -> Optional:
    """Redis 클라이언트를 반환합니다."""
    try:
        client = get_redis_connection()
        # 연결 테스트
        client.ping()
        return client
    except (ConnectionError, Exception) as e:
        logger.warning("Redis 연결 실패, 락 기능 비활성화: {}", e)
        return None


@contextmanager
def acquire_lock(
    lock_key: str,
    timeout: float = 3600.0,  # 기본 1시간 (작업 최대 시간)
    blocking_timeout: float = 0.0,  # 기본 0초 (즉시 실패)
):
    """
    Redis 기반 분산 락을 획득하는 컨텍스트 매니저.
    
    Args:
        lock_key: 락 키 (예: "lock:asr:global", "lock:asr:{file_id}", "lock:llm:global", "lock:llm:{file_id}", "lock:ocr:global", "lock:ocr:{file_id}")
        timeout: 락 자동 해제 시간 (초, TTL). 
                 이 시간이 지나면 자동으로 락이 해제됩니다.
                 워커가 강제 종료되어도 TTL이 지나면 락이 자동 해제됩니다.
                 권장: 오디오 파일 길이의 0.5배 (최소 300초, 최대 7200초)
        blocking_timeout: 락 획득 대기 시간 (초). 0이면 즉시 실패합니다.
    
    Yields:
        bool: 락을 획득했으면 True, 실패했으면 False
    
    Example:
        # 오디오 길이 기반 TTL 계산
        audio_duration = 1800.0  # 30분
        lock_ttl = max(300.0, min(audio_duration * 0.5, 7200.0))  # 900초
        
        with acquire_lock("lock:asr:123", timeout=lock_ttl) as acquired:
            if acquired:
                # 락 획득 성공, 작업 수행
                pass
            else:
                # 락 획득 실패, 작업 스킵
                pass
    """
    redis_client = _get_redis_client()
    lock = None
    acquired = False
    
    if redis_client is None:
        # Redis가 없으면 락 없이 진행 (하위 호환성)
        logger.warning("Redis가 없어 락 없이 진행합니다: {}", lock_key)
        yield True
        return
    
    try:
        # Redis Lock 생성
        lock = redis_client.lock(
            lock_key,
            timeout=timeout,
            blocking_timeout=blocking_timeout,
            thread_local=False,  # 멀티스레드 환경에서도 동작하도록
        )
        
        # 락 획득 시도
        # blocking_timeout이 0보다 크면 블로킹 모드 사용
        is_blocking = blocking_timeout > 0
        acquired = lock.acquire(blocking=is_blocking)
        
        if acquired:
            logger.info("락 획득 성공: {}", lock_key)
        else:
            # 락 획득 실패 시 TTL 정보 조회
            ttl_seconds = None
            try:
                ttl_seconds = redis_client.ttl(lock_key)
                if ttl_seconds > 0:
                    # TTL을 읽기 쉬운 형식으로 변환
                    minutes = ttl_seconds // 60
                    seconds = ttl_seconds % 60
                    if minutes > 0:
                        ttl_str = f"{minutes}분 {seconds}초"
                    else:
                        ttl_str = f"{seconds}초"
                    logger.warning(
                        "락 획득 실패 (다른 워커가 처리 중): {}, 락 해제 예상 시간: {} (TTL: {}초)",
                        lock_key,
                        ttl_str,
                        ttl_seconds
                    )
                elif ttl_seconds == -1:
                    # TTL이 없는 경우 (이론적으로는 발생하지 않아야 함)
                    logger.warning(
                        "락 획득 실패 (다른 워커가 처리 중): {}, 락 TTL 정보 없음",
                        lock_key
                    )
                else:
                    # ttl_seconds == -2: 키가 존재하지 않음
                    logger.warning(
                        "락 획득 실패: {}, 락이 존재하지 않음 (이미 해제됨)",
                        lock_key
                    )
            except Exception as e:
                logger.warning(
                    "락 획득 실패 (다른 워커가 처리 중): {}, TTL 조회 실패: {}",
                    lock_key,
                    e
                )
        
        try:
            yield acquired
        except GeneratorExit:
            # 컨텍스트 매니저가 정상적으로 종료될 때 발생
            # 제너레이터가 정상적으로 종료되도록 보장
            if lock and acquired:
                try:
                    lock.release()
                    logger.info("락 해제 완료 (GeneratorExit): {}", lock_key)
                except Exception:
                    pass
            raise
        except Retry:
            # Celery의 Retry 예외는 재시도를 위해 그대로 전파해야 함
            # 락은 finally에서 해제됨
            # 제너레이터가 정상적으로 종료되도록 보장
            if lock and acquired:
                try:
                    lock.release()
                    logger.info("락 해제 완료 (Retry): {}", lock_key)
                except Exception:
                    pass
            raise
        except Exception:
            # 다른 예외는 그대로 전파 (락은 finally에서 해제됨)
            raise
        
    except Retry:
        # yield 전에 Retry가 발생한 경우 (거의 없지만 안전을 위해)
        raise
    except (LockError, ConnectionError, Exception) as e:
        logger.error("락 획득 중 오류 발생: {}, error={}", lock_key, e)
        try:
            yield False
        except GeneratorExit:
            # 제너레이터가 정상적으로 종료되도록 보장
            raise
        except Exception:
            # 예외가 발생해도 제너레이터는 정상 종료
            raise
    finally:
        if lock and acquired:
            try:
                lock.release()
                logger.info("락 해제 완료: {}", lock_key)
            except Exception as e:
                logger.warning("락 해제 중 오류 발생: {}, error={}", lock_key, e)


def is_locked(lock_key: str) -> bool:
    """
    락이 현재 획득되어 있는지 확인합니다.
    
    Args:
        lock_key: 확인할 락 키
    
    Returns:
        락이 획득되어 있으면 True, 아니면 False
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        return False
    
    try:
        # Redis에서 락 키가 존재하는지 확인
        return redis_client.exists(lock_key) > 0
    except Exception as e:
        logger.warning("락 확인 중 오류 발생: {}, error={}", lock_key, e)
        return False


def _check_worker_active_by_queue(queue_name: str) -> bool:
    """
    특정 큐의 워커가 활성 작업을 처리 중인지 확인합니다.
    
    Args:
        queue_name: 확인할 큐 이름 ("asr", "ocr", "llm")
    
    Returns:
        해당 큐의 작업이 실행 중이면 True, 아니면 False
    """
    try:
        from .celery_app import celery_app
        
        # 큐 이름에 따른 작업 이름 매핑
        task_name_map = {
            "asr": "process_asr_task",
            "ocr": "process_ocr_task",
            "llm": "process_llm_task",
        }
        
        task_name = task_name_map.get(queue_name)
        if not task_name:
            logger.warning("Unknown queue name: {}, returning False", queue_name)
            return False
        
        # Celery inspect를 사용하여 활성 작업 확인
        inspect = celery_app.control.inspect()
        active_tasks = inspect.active()
        
        if not active_tasks:
            return False
        
        # 모든 워커의 활성 작업 확인
        for worker_name, tasks in active_tasks.items():
            for task in tasks:
                # 해당 큐의 작업이 진행 중인지 확인
                if task.get("name") == task_name:
                    logger.info("{} task is active: worker={}, task_id={}", queue_name.upper(), worker_name, task.get("id"))
                    return True
        
        return False
    except Exception as e:
        logger.warning("Failed to check Celery active tasks for queue {}: {}", queue_name, e)
        # 확인 실패 시 False 반환 (락으로 제어하므로 안전)
        return False


def _check_celery_task_active() -> bool:
    """
    Celery에서 LLM 작업이 실제로 진행 중인지 확인합니다 (하위 호환성).
    
    Returns:
        작업이 진행 중이면 True, 아니면 False
    """
    return _check_worker_active_by_queue("llm")


@contextmanager
def acquire_task_locks(
    queue_name: str,
    file_id: int,
    task_id: str,
    lock_ttl: float,
):
    """
    태스크 실행을 위한 전역 락과 개별 락을 획득하는 컨텍스트 매니저.
    OCR 방식을 기준으로 워커 상태 확인 및 락 강제 해제 로직을 포함합니다.
    
    Args:
        queue_name: 큐 이름 ("asr", "ocr", "llm")
        file_id: 파일 ID
        task_id: 태스크 ID (로깅용)
        lock_ttl: 락 TTL (초)
    
    Yields:
        tuple[bool, bool]: (전역 락 획득 여부, 개별 락 획득 여부)
    
    Example:
        with acquire_task_locks("asr", file_id=123, task_id="task-123", lock_ttl=3600.0) as (global_acquired, individual_acquired):
            if not global_acquired:
                return {"status": "skipped", "reason": "global_lock_failed"}
            if not individual_acquired:
                return {"status": "skipped", "reason": "individual_lock_failed"}
            # 작업 수행
    """
    global_lock_key = f"lock:{queue_name}:global"
    individual_lock_key = f"lock:{queue_name}:{file_id}"
    
    # 워커 상태 확인: 락이 존재하지만 워커가 비활성화되어 있으면 락을 강제 해제
    if is_locked(global_lock_key):
        worker_active = _check_worker_active_by_queue(queue_name)
        if not worker_active:
            logger.warning(
                "[Celery {}] Lock exists but worker is inactive, forcing release: file_id={}, task_id={}",
                queue_name.upper(),
                file_id,
                task_id,
            )
            # 락 강제 해제 시도
            try:
                redis_client = _get_redis_client()
                if redis_client:
                    redis_client.delete(global_lock_key)
                    logger.info("[Celery {}] Forced lock release: {}", queue_name.upper(), global_lock_key)
            except Exception as e:
                logger.warning(
                    "[Celery {}] Failed to force release lock: {}, error={}",
                    queue_name.upper(),
                    global_lock_key,
                    e
                )
    
    # 전역 락 획득 (워커 상태 확인 후)
    with acquire_lock(global_lock_key, timeout=lock_ttl, blocking_timeout=0.0) as global_acquired:
        if not global_acquired:
            logger.warning(
                "[Celery {}] Task skipped (global lock failed): file_id={}, task_id={}",
                queue_name.upper(),
                file_id,
                task_id,
            )
            yield (False, False)
            return
        
        # 개별 ID 락 획득
        with acquire_lock(individual_lock_key, timeout=lock_ttl, blocking_timeout=0.0) as individual_acquired:
            if not individual_acquired:
                logger.warning(
                    "[Celery {}] Task skipped (individual lock failed): file_id={}, task_id={}",
                    queue_name.upper(),
                    file_id,
                    task_id,
                )
                yield (True, False)
                return
            
            # 두 락 모두 획득 성공
            yield (True, True)


def force_release_lock(lock_key: str, check_active: bool = True) -> bool:
    """
    락을 강제로 해제합니다.
    
    Args:
        lock_key: 해제할 락 키
        check_active: True일 때 실제 작업 진행 여부를 확인하고, 진행 중이면 해제하지 않음
    
    Returns:
        락이 해제되었으면 True, 해제하지 못했으면 False
    """
    redis_client = _get_redis_client()
    if redis_client is None:
        logger.warning("Redis가 없어 락을 강제 해제할 수 없습니다: {}", lock_key)
        return False
    
    try:
        # 락이 존재하는지 확인
        if not redis_client.exists(lock_key):
            logger.info("락이 존재하지 않습니다: {}", lock_key)
            return False
        
        # check_active가 True일 때 실제 작업 진행 여부 확인
        if check_active:
            if _check_celery_task_active():
                logger.warning(
                    "LLM 작업이 진행 중이므로 락을 강제 해제하지 않습니다: {}",
                    lock_key
                )
                return False
        
        # 락 강제 해제
        # Redis Lock은 소유권을 확인하므로, 강제 해제를 위해서는 키를 직접 삭제
        try:
            # 락 키와 관련된 모든 키 삭제 (Redis Lock은 여러 키를 사용할 수 있음)
            # 기본적으로 lock_key 자체를 삭제
            deleted = redis_client.delete(lock_key)
            if deleted > 0:
                logger.info("락 키 삭제 완료: {} (deleted={})", lock_key, deleted)
                return True
            else:
                logger.warning("락 키가 이미 존재하지 않습니다: {}", lock_key)
                return False
        except Exception as e:
            logger.error("락 키 삭제 실패: {}, error={}", lock_key, e)
            return False
    
    except Exception as e:
        logger.error("락 강제 해제 중 오류 발생: {}, error={}", lock_key, e)
        return False