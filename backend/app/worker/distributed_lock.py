"""Redis 기반 분산 락 유틸리티."""
from contextlib import contextmanager
from typing import Optional

from celery.exceptions import Retry
from redis.exceptions import ConnectionError, LockError

from ..core.logging import logger
from ..core.redis import get_redis_connection


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
        lock_key: 락 키 (예: "lock:asr:123" 또는 "lock:llm:123")
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
        acquired = lock.acquire(blocking=False)
        
        if acquired:
            logger.info("락 획득 성공: {}", lock_key)
        else:
            logger.warning("락 획득 실패 (다른 워커가 처리 중): {}", lock_key)
        
        try:
            yield acquired
        except GeneratorExit:
            # 컨텍스트 매니저가 정상적으로 종료될 때 발생
            raise
        except Retry:
            # Celery의 Retry 예외는 재시도를 위해 그대로 전파해야 함
            # 락은 finally에서 해제됨
            raise
        except Exception:
            # 다른 예외는 그대로 전파 (락은 finally에서 해제됨)
            raise
        
    except Retry:
        # yield 전에 Retry가 발생한 경우 (거의 없지만 안전을 위해)
        raise
    except (LockError, ConnectionError, Exception) as e:
        logger.error("락 획득 중 오류 발생: {}, error={}", lock_key, e)
        yield False
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

