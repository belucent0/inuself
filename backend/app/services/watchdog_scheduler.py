"""Watchdog 스케줄러.

주기적으로 StateWatchdog를 실행하여 stuck 상태를 감지하고 복구합니다.
"""
import asyncio
from datetime import datetime, timezone

from ..core.logging import logger
from .agent_job_recovery import recover_stale_agent_jobs
from .state_watchdog import run_watchdog_scan
from .state_reconciler import run_reconciler


class WatchdogScheduler:
    """StateWatchdog 주기적 실행 스케줄러.

    지정된 간격으로 watchdog 스캔을 실행하고,
    발견된 문제에 대해 자동으로 reconcile을 수행합니다.
    """

    def __init__(self, interval_minutes: int = 5, auto_reconcile: bool = True):
        """
        Args:
            interval_minutes: 스캔 간격 (분)
            auto_reconcile: True면 문제 발견 시 자동으로 reconcile 실행
        """
        self.interval_minutes = interval_minutes
        self.auto_reconcile = auto_reconcile
        self._running = False
        self._last_run: datetime | None = None
        self._run_count = 0
        self._issue_count = 0

    async def start(self) -> None:
        """스케줄러를 시작합니다."""
        self._running = True
        logger.info(
            f"[WatchdogScheduler] Started (interval: {self.interval_minutes}m, "
            f"auto_reconcile: {self.auto_reconcile})"
        )

        while self._running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                logger.info("[WatchdogScheduler] Cancelled")
                break
            except Exception as e:
                logger.error(f"[WatchdogScheduler] Error in cycle: {e}")

            # 다음 실행까지 대기
            try:
                await asyncio.sleep(self.interval_minutes * 60)
            except asyncio.CancelledError:
                break

        logger.info("[WatchdogScheduler] Stopped")

    def stop(self) -> None:
        """스케줄러를 중지합니다."""
        self._running = False

    async def _run_cycle(self) -> None:
        """단일 watchdog 사이클을 실행합니다."""
        self._run_count += 1
        self._last_run = datetime.now(timezone.utc)

        logger.info(f"[WatchdogScheduler] Running cycle #{self._run_count}")

        try:
            # Watchdog 스캔
            try:
                recovered = await recover_stale_agent_jobs()
                if recovered:
                    logger.warning(
                        f"[WatchdogScheduler] Requeued {recovered} stale agent job(s)"
                    )
            except Exception as exc:
                logger.error(f"[WatchdogScheduler] Agent recovery failed: {exc}")

            findings = await run_watchdog_scan()

            if not findings:
                logger.debug("[WatchdogScheduler] No issues found")
                return

            self._issue_count += len(findings)
            logger.warning(
                f"[WatchdogScheduler] Found {len(findings)} issues "
                f"(total: {self._issue_count})"
            )

            # 자동 reconcile
            if self.auto_reconcile:
                logger.info("[WatchdogScheduler] Running auto-reconcile...")
                actions = await run_reconciler(findings)

                successful = sum(1 for a in actions if a.success)
                logger.info(
                    f"[WatchdogScheduler] Reconcile completed: "
                    f"{successful}/{len(actions)} successful"
                )

        except Exception as e:
            logger.error(f"[WatchdogScheduler] Cycle failed: {e}")
            raise

    @property
    def stats(self) -> dict:
        """스케줄러 통계를 반환합니다."""
        return {
            "running": self._running,
            "interval_minutes": self.interval_minutes,
            "auto_reconcile": self.auto_reconcile,
            "run_count": self._run_count,
            "total_issues_found": self._issue_count,
            "last_run": self._last_run.isoformat() if self._last_run else None,
        }
