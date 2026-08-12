"""vLLM 연속 실패 감지 circuit breaker.

LLM 요약 partial retry loop가 vLLM 영구 다운 상태에서 무한히 백오프 라운드를
돌지 않도록 회로를 차단한다. threshold 연속 실패 시 cooldown 동안 즉시 fail.

비동기 단일 프로세스 가정. 다중 worker 환경에서도 각 프로세스가 자기 호출의
연속 실패만 카운트하면 충분 (vLLM 다운은 모든 워커가 동시 감지).
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class CircuitBreakerOpenError(Exception):
    """회로가 열린 동안 호출 시도 시 발생."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        threshold: int = 10,
        cooldown_seconds: float = 300.0,
    ) -> None:
        self.name = name
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._failure_count = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            # cooldown 경과 → half-open (다음 호출이 다시 닫음)
            logger.info(
                f"[CircuitBreaker:{self.name}] cooldown 경과, half-open 진입"
            )
            self._opened_at = None
            self._failure_count = 0
            return False
        return True

    def record_success(self) -> None:
        if self._failure_count > 0 or self._opened_at is not None:
            logger.info(f"[CircuitBreaker:{self.name}] 성공으로 회로 재닫음")
        self._failure_count = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self.threshold and self._opened_at is None:
            self._opened_at = time.monotonic()
            logger.warning(
                f"[CircuitBreaker:{self.name}] {self.threshold}회 연속 실패 → "
                f"{self.cooldown_seconds}초 동안 회로 차단"
            )

    def assert_closed(self) -> None:
        """회로가 열렸으면 즉시 예외. partial retry loop의 라운드 진입 직전 호출."""
        if self.is_open:
            remaining = (
                self.cooldown_seconds - (time.monotonic() - (self._opened_at or 0))
                if self._opened_at
                else 0
            )
            raise CircuitBreakerOpenError(
                f"{self.name} 회로 차단 중 (남은 cooldown: {remaining:.0f}s)"
            )


# vLLM 호출 전역 인스턴스 (요약 + chat 등 모든 vLLM 경유 호출 공통)
vllm_breaker = CircuitBreaker(name="vllm", threshold=10, cooldown_seconds=300.0)
