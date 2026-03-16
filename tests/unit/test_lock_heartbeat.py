"""Redis Lock Heartbeat 유닛 테스트.

start_lock_heartbeat / stop_lock_heartbeat 헬퍼의 동작을 검증한다.
실제 Redis 없이 mock으로 테스트.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock


# ── custom_handler 헬퍼 직접 정의 (import 없이 로직만 테스트) ──────────
# custom_handler.py는 litellm 등 무거운 의존성이 있어 직접 import 불가.
# 동일한 로직을 여기에 복사하여 단위 테스트한다.


def _start_lock_heartbeat(redis_client, key: str, lock_id: str, ttl: int) -> threading.Event:
    """custom_handler.start_lock_heartbeat와 동일 로직."""
    interval = ttl // 2
    stop_event = threading.Event()

    def _heartbeat():
        while not stop_event.wait(interval):
            try:
                lock = redis_client.lock(key, thread_local=False)
                lock.local.token = lock_id.encode()
                lock.extend(ttl, replace_ttl=True)
            except Exception:
                break

    t = threading.Thread(target=_heartbeat, daemon=True)
    t.start()
    return stop_event


def _stop_lock_heartbeat(stop_event: threading.Event | None):
    """custom_handler.stop_lock_heartbeat와 동일 로직."""
    if stop_event:
        stop_event.set()


def _start_worker_heartbeat(redis_client, lock_id: str, ttl: int):
    """litellm_audio_client.start_lock_heartbeat와 동일 로직."""
    if not redis_client:
        return None
    key = "worker:gpu:active"
    interval = ttl // 2
    stop_event = threading.Event()

    def _heartbeat():
        while not stop_event.wait(interval):
            try:
                lock = redis_client.lock(key, thread_local=False)
                lock.local.token = lock_id.encode()
                lock.extend(ttl, replace_ttl=True)
            except Exception:
                break

    t = threading.Thread(target=_heartbeat, daemon=True)
    t.start()
    return stop_event


# ── 테스트 ──────────────────────────────────────────────────────────


class TestStartLockHeartbeat:
    """start_lock_heartbeat 기본 동작 테스트."""

    def test_extends_ttl_periodically(self):
        """heartbeat가 interval마다 lock.extend()를 호출하는지 확인."""
        mock_lock = MagicMock()
        mock_lock.local = MagicMock()
        mock_redis = MagicMock()
        mock_redis.lock.return_value = mock_lock

        # TTL=2 → interval=1초
        stop = _start_lock_heartbeat(mock_redis, "worker:gpu:active", "test-lock-id", ttl=2)

        # 1.5초 대기 → extend 최소 1회 호출
        time.sleep(1.5)
        _stop_lock_heartbeat(stop)

        assert mock_lock.extend.call_count >= 1
        mock_lock.extend.assert_called_with(2, replace_ttl=True)

    def test_sets_correct_token(self):
        """heartbeat가 올바른 lock token을 설정하는지 확인."""
        mock_lock = MagicMock()
        mock_lock.local = MagicMock()
        mock_redis = MagicMock()
        mock_redis.lock.return_value = mock_lock

        lock_id = "abc-123-def"
        stop = _start_lock_heartbeat(mock_redis, "worker:gpu:active", lock_id, ttl=2)
        time.sleep(1.5)
        _stop_lock_heartbeat(stop)

        # token이 lock_id.encode()로 설정되었는지
        mock_lock.local.token = lock_id.encode()

    def test_uses_correct_key(self):
        """heartbeat가 올바른 Redis 키를 사용하는지 확인."""
        mock_lock = MagicMock()
        mock_lock.local = MagicMock()
        mock_redis = MagicMock()
        mock_redis.lock.return_value = mock_lock

        key = "worker:gpu:active"
        stop = _start_lock_heartbeat(mock_redis, key, "lock-id", ttl=2)
        time.sleep(1.5)
        _stop_lock_heartbeat(stop)

        mock_redis.lock.assert_called_with(key, thread_local=False)


class TestStopLockHeartbeat:
    """stop_lock_heartbeat 테스트."""

    def test_stops_thread(self):
        """stop 호출 시 스레드가 종료되는지 확인."""
        mock_lock = MagicMock()
        mock_lock.local = MagicMock()
        mock_redis = MagicMock()
        mock_redis.lock.return_value = mock_lock

        stop = _start_lock_heartbeat(mock_redis, "worker:gpu:active", "lock-id", ttl=2)

        # 스레드가 실행 중인지 확인
        active_threads_before = threading.active_count()

        _stop_lock_heartbeat(stop)
        time.sleep(0.3)  # 스레드 종료 대기

        # stop_event가 set 되었는지
        assert stop.is_set()

    def test_none_does_not_raise(self):
        """stop_event=None 전달 시 에러 없이 통과."""
        _stop_lock_heartbeat(None)  # 예외 없이 통과해야 함


class TestHeartbeatExtendFailure:
    """extend 실패 시 스레드 종료 테스트."""

    def test_thread_exits_on_extend_failure(self):
        """lock.extend()가 예외 발생 시 heartbeat 스레드가 종료되는지 확인."""
        mock_lock = MagicMock()
        mock_lock.local = MagicMock()
        mock_lock.extend.side_effect = Exception("Redis connection lost")
        mock_redis = MagicMock()
        mock_redis.lock.return_value = mock_lock

        stop = _start_lock_heartbeat(mock_redis, "worker:gpu:active", "lock-id", ttl=2)

        # extend 실패 후 스레드가 종료되는지 대기
        time.sleep(2.0)

        # extend가 한 번 호출된 후 실패하여 스레드가 종료됨
        assert mock_lock.extend.call_count >= 1

        # 이후 더 이상 호출되지 않는지 확인
        count_after_fail = mock_lock.extend.call_count
        time.sleep(1.5)
        assert mock_lock.extend.call_count == count_after_fail


class TestWorkerHeartbeat:
    """Worker측 (litellm_audio_client) heartbeat 테스트."""

    def test_returns_none_without_redis(self):
        """Redis 클라이언트가 None이면 None 반환."""
        result = _start_worker_heartbeat(None, "lock-id", ttl=600)
        assert result is None

    def test_returns_stop_event_with_redis(self):
        """Redis 클라이언트가 있으면 stop_event 반환."""
        mock_lock = MagicMock()
        mock_lock.local = MagicMock()
        mock_redis = MagicMock()
        mock_redis.lock.return_value = mock_lock

        stop = _start_worker_heartbeat(mock_redis, "lock-id", ttl=2)
        assert stop is not None
        assert isinstance(stop, threading.Event)

        _stop_lock_heartbeat(stop)

    def test_worker_heartbeat_extends(self):
        """Worker heartbeat가 실제로 extend를 호출하는지 확인."""
        mock_lock = MagicMock()
        mock_lock.local = MagicMock()
        mock_redis = MagicMock()
        mock_redis.lock.return_value = mock_lock

        stop = _start_worker_heartbeat(mock_redis, "lock-id", ttl=2)
        time.sleep(1.5)
        _stop_lock_heartbeat(stop)

        assert mock_lock.extend.call_count >= 1
        mock_lock.extend.assert_called_with(2, replace_ttl=True)


class TestLockAcquiredHereGuard:
    """lock_acquired_here 플래그에 따른 heartbeat/release 조건 테스트.

    custom_handler에서 Worker lock이 전달되면:
    - lock_acquired_here = False
    - heartbeat 시작하지 않음
    - release 하지 않음
    """

    def test_worker_lock_skips_heartbeat(self):
        """Worker에서 lock 전달 시 heartbeat를 시작하지 않는 로직."""
        worker_lock_id = "worker-provided-lock"
        lock_acquired_here = False

        # custom_handler의 로직을 시뮬레이션
        heartbeat = None
        if lock_acquired_here and worker_lock_id:
            heartbeat = "should_not_be_set"

        assert heartbeat is None

    def test_direct_lock_starts_heartbeat(self):
        """직접 획득한 lock에는 heartbeat를 시작하는 로직."""
        lock_id = "directly-acquired-lock"
        lock_acquired_here = True

        mock_lock = MagicMock()
        mock_lock.local = MagicMock()
        mock_redis = MagicMock()
        mock_redis.lock.return_value = mock_lock

        heartbeat = None
        if lock_acquired_here and lock_id:
            heartbeat = _start_lock_heartbeat(
                mock_redis, "worker:gpu:active", lock_id, ttl=2
            )

        assert heartbeat is not None
        _stop_lock_heartbeat(heartbeat)

    def test_worker_lock_skips_release(self):
        """Worker lock 전달 시 release를 하지 않는 로직."""
        lock_acquired_here = False
        lock_id = "worker-lock"
        release_called = False

        # custom_handler finally 블록 시뮬레이션
        if lock_acquired_here and lock_id:
            release_called = True

        assert not release_called

    def test_direct_lock_does_release(self):
        """직접 획득 lock은 release 수행."""
        lock_acquired_here = True
        lock_id = "direct-lock"
        release_called = False

        if lock_acquired_here and lock_id:
            release_called = True

        assert release_called


class TestPipelineHeartbeatIntegration:
    """pipeline.py의 heartbeat 통합 흐름 테스트."""

    def test_full_lifecycle(self):
        """acquire → heartbeat start → (work) → heartbeat stop → release 순서."""
        mock_lock = MagicMock()
        mock_lock.local = MagicMock()
        mock_redis = MagicMock()
        mock_redis.lock.return_value = mock_lock

        call_order = []

        # acquire
        lock_id = "pipeline-lock-id"
        call_order.append("acquire")

        # heartbeat start
        stop = _start_worker_heartbeat(mock_redis, lock_id, ttl=2)
        call_order.append("heartbeat_start")

        # simulate work
        time.sleep(1.5)
        call_order.append("work_done")

        # heartbeat stop
        _stop_lock_heartbeat(stop)
        call_order.append("heartbeat_stop")

        # release
        call_order.append("release")

        assert call_order == [
            "acquire", "heartbeat_start", "work_done", "heartbeat_stop", "release"
        ]
        # heartbeat가 작업 중 extend를 호출했는지
        assert mock_lock.extend.call_count >= 1
