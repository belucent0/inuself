"""CircuitBreaker 단위 테스트."""

import time

import pytest

from app.services.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


def test_closed_by_default():
    cb = CircuitBreaker(name="test", threshold=3, cooldown_seconds=10)
    assert cb.is_open is False
    cb.assert_closed()  # should not raise


def test_opens_after_threshold():
    cb = CircuitBreaker(name="test", threshold=3, cooldown_seconds=10)
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open is True
    with pytest.raises(CircuitBreakerOpenError):
        cb.assert_closed()


def test_success_resets_count():
    cb = CircuitBreaker(name="test", threshold=3, cooldown_seconds=10)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    # count resets to 0 after success, so 1 failure < threshold
    assert cb.is_open is False


def test_cooldown_transitions_to_half_open(monkeypatch):
    cb = CircuitBreaker(name="test", threshold=2, cooldown_seconds=0.05)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open is True

    time.sleep(0.06)
    # cooldown 경과 후 자동 half-open
    assert cb.is_open is False
    cb.assert_closed()  # half-open allows new call
