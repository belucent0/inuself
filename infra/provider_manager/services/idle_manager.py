"""Idle Timeout Manager - On-Demand 프로바이더 자동 언로드.

모듈화된 idle timeout 관리자.
일정 시간 동안 요청이 없는 On-Demand 프로바이더를 자동으로 종료하여 VRAM을 해제합니다.

Usage:
    from services.idle_manager import IdleTimeoutManager

    idle_manager = IdleTimeoutManager(provider_manager, idle_timeout=60)
    await idle_manager.start()
"""
import asyncio
import logging
import time
from typing import TYPE_CHECKING, Optional, Callable, Awaitable

if TYPE_CHECKING:
    from core.manager import ProviderManager, ProviderStatus

logger = logging.getLogger("IdleTimeoutManager")


class IdleTimeoutManager:
    """On-Demand 프로바이더의 idle timeout을 관리하는 모듈.

    Features:
    - 마지막 활동 시간 추적
    - 주기적인 idle 체크
    - timeout 시 자동 종료
    - On-Demand 프로바이더만 대상 (enabled=False)

    Args:
        provider_manager: ProviderManager 인스턴스
        idle_timeout: idle timeout 시간 (초, 기본값 60)
        check_interval: idle 체크 주기 (초, 기본값 10)
        on_idle_stop: 프로바이더 종료 시 호출되는 콜백 (선택)
    """

    def __init__(
        self,
        provider_manager: "ProviderManager",
        idle_timeout: float = 60.0,
        check_interval: float = 10.0,
        on_idle_stop: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.provider_manager = provider_manager
        self.idle_timeout = idle_timeout
        self.check_interval = check_interval
        self.on_idle_stop = on_idle_stop

        # 프로바이더별 마지막 활동 시간 {provider_name: timestamp}
        self._last_activity: dict[str, float] = {}

        # 실행 플래그
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def record_activity(self, provider_name: str) -> None:
        """프로바이더 활동 기록 (요청 시작/완료 시 호출).

        Args:
            provider_name: 프로바이더 이름
        """
        self._last_activity[provider_name] = time.time()
        logger.debug(f"[IdleManager] Activity recorded: {provider_name}")

    def get_idle_time(self, provider_name: str) -> float:
        """프로바이더의 현재 idle 시간 반환 (초).

        Args:
            provider_name: 프로바이더 이름

        Returns:
            idle 시간 (초). 활동 기록이 없으면 0 반환.
        """
        if provider_name not in self._last_activity:
            return 0.0
        return time.time() - self._last_activity[provider_name]

    def _is_on_demand_provider(self, provider_name: str) -> bool:
        """On-Demand 프로바이더인지 확인 (enabled=False).

        Args:
            provider_name: 프로바이더 이름

        Returns:
            On-Demand 프로바이더이면 True
        """
        for group in self.provider_manager.groups.values():
            for provider in group.providers:
                if provider.name == provider_name:
                    return not provider.enabled
        return False

    def _is_provider_running(self, provider_name: str) -> bool:
        """프로바이더가 현재 실행 중인지 확인.

        Args:
            provider_name: 프로바이더 이름

        Returns:
            실행 중이면 True
        """
        from core.manager import ProviderStatus

        state = self.provider_manager.provider_states.get(provider_name)
        if not state:
            return False
        return state.status == ProviderStatus.UP

    def _has_active_jobs(self, provider_name: str) -> bool:
        """프로바이더에 활성 작업이 있는지 확인.

        Args:
            provider_name: 프로바이더 이름

        Returns:
            활성 작업이 있으면 True
        """
        state = self.provider_manager.provider_states.get(provider_name)
        if not state:
            return False
        return state.active_jobs > 0

    async def _check_and_stop_idle_providers(self) -> None:
        """idle timeout된 On-Demand 프로바이더를 종료."""
        from core.manager import ProviderStatus

        current_time = time.time()

        for provider_name, last_activity in list(self._last_activity.items()):
            # On-Demand 프로바이더만 대상
            if not self._is_on_demand_provider(provider_name):
                continue

            # 실행 중이 아니면 스킵
            if not self._is_provider_running(provider_name):
                continue

            # 활성 작업이 있으면 스킵
            if self._has_active_jobs(provider_name):
                logger.debug(f"[IdleManager] {provider_name} has active jobs, skipping idle check")
                continue

            # idle timeout 체크
            idle_time = current_time - last_activity
            if idle_time >= self.idle_timeout:
                logger.info(
                    f"[IdleManager] {provider_name} idle for {idle_time:.1f}s "
                    f"(timeout: {self.idle_timeout}s), stopping..."
                )

                # 프로바이더 종료
                await self.provider_manager.stop_provider(provider_name)

                # 활동 기록 제거
                del self._last_activity[provider_name]

                # 콜백 호출
                if self.on_idle_stop:
                    try:
                        await self.on_idle_stop(provider_name)
                    except Exception as e:
                        logger.warning(f"[IdleManager] on_idle_stop callback failed: {e}")

                logger.info(f"[IdleManager] {provider_name} stopped due to idle timeout")

    async def _monitor_loop(self) -> None:
        """idle 체크 메인 루프."""
        logger.info(
            f"[IdleManager] Started (timeout={self.idle_timeout}s, interval={self.check_interval}s)"
        )

        while self._running:
            try:
                await self._check_and_stop_idle_providers()
            except Exception as e:
                logger.error(f"[IdleManager] Error in monitor loop: {e}")

            await asyncio.sleep(self.check_interval)

        logger.info("[IdleManager] Stopped")

    async def start(self) -> None:
        """idle timeout 모니터링 시작."""
        if self._running:
            logger.warning("[IdleManager] Already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("[IdleManager] Monitor task created")

    def stop(self) -> None:
        """idle timeout 모니터링 중지."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("[IdleManager] Stopped")

    def get_status(self) -> dict:
        """현재 상태 반환 (디버깅/모니터링용).

        Returns:
            {
                "running": bool,
                "idle_timeout": float,
                "check_interval": float,
                "tracked_providers": {
                    "provider_name": {
                        "last_activity": timestamp,
                        "idle_time": float,
                        "is_on_demand": bool,
                        "is_running": bool,
                    }
                }
            }
        """
        current_time = time.time()
        tracked = {}

        for provider_name, last_activity in self._last_activity.items():
            tracked[provider_name] = {
                "last_activity": last_activity,
                "idle_time": current_time - last_activity,
                "is_on_demand": self._is_on_demand_provider(provider_name),
                "is_running": self._is_provider_running(provider_name),
            }

        return {
            "running": self._running,
            "idle_timeout": self.idle_timeout,
            "check_interval": self.check_interval,
            "tracked_providers": tracked,
        }
