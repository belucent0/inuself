"""Provider Service - 비즈니스 로직 통합 레이어.

HTTP API와 Redis Stream API에서 공통으로 사용하는 비즈니스 로직.
로직 중복을 방지하고 단일 관리 포인트를 제공합니다.
"""

import time
import logging
from typing import Optional, Dict, Any, List

from core.manager import ProviderManager
from services.job_tracker import JobTracker, JobStatus

logger = logging.getLogger("ProviderService")


class ProviderService:
    """Provider Manager 비즈니스 로직 서비스.

    HTTP API와 Stream API 모두 이 서비스를 통해 로직을 실행합니다.
    """

    def __init__(
        self,
        manager: ProviderManager,
        job_tracker: Optional[JobTracker] = None,
    ):
        self.manager = manager
        self.job_tracker = job_tracker

    # ==========================================
    # Provider Operations
    # ==========================================

    async def list_providers(self) -> Dict[str, Any]:
        """프로바이더 목록 조회."""
        running = list(self.manager.processes.keys())
        providers = []

        for group in self.manager.get_enabled_groups():
            for provider in group.providers:
                providers.append({
                    "name": provider.name,
                    "port": provider.port,
                    "health": provider.health,
                    "enabled": provider.enabled,
                    "running": provider.name in running,
                    "group": group.name,
                })

        return {
            "providers": providers,
            "total": len(providers),
            "running_count": len(running),
        }

    async def get_provider(self, name: str) -> Optional[Dict[str, Any]]:
        """단일 프로바이더 조회."""
        running = list(self.manager.processes.keys())

        for group in self.manager.get_enabled_groups():
            for provider in group.providers:
                if provider.name == name:
                    return {
                        "name": provider.name,
                        "port": provider.port,
                        "health": provider.health,
                        "enabled": provider.enabled,
                        "running": provider.name in running,
                        "group": group.name,
                    }
        return None

    async def load_provider(self, name: str) -> Dict[str, Any]:
        """프로바이더 로드."""
        await self.manager.load_provider(name)
        return {
            "provider": name,
            "status": "loaded",
        }

    async def unload_provider(self, name: str) -> Dict[str, Any]:
        """프로바이더 언로드."""
        await self.manager.unload_provider(name)
        return {
            "provider": name,
            "status": "unloaded",
        }

    async def reload_provider(self, name: str) -> Dict[str, Any]:
        """프로바이더 재로드."""
        await self.manager.reload_provider(name)
        return {
            "provider": name,
            "status": "reloaded",
        }

    async def enable_provider(self, name: str) -> Dict[str, Any]:
        """프로바이더 활성화."""
        self.manager.enable_provider(name)
        return {
            "provider": name,
            "status": "enabled",
        }

    async def disable_provider(self, name: str) -> Dict[str, Any]:
        """프로바이더 비활성화."""
        self.manager.disable_provider(name)
        return {
            "provider": name,
            "status": "disabled",
        }

    # ==========================================
    # Group Operations
    # ==========================================

    async def list_groups(self) -> Dict[str, Any]:
        """그룹 목록 조회."""
        running = list(self.manager.processes.keys())
        groups = []

        for group in self.manager.get_enabled_groups():
            running_count = sum(1 for p in group.providers if p.name in running)
            groups.append({
                "name": group.name,
                "order": group.order,
                "enabled": group.enabled,
                "provider_count": len(group.providers),
                "running_count": running_count,
                "providers": [p.name for p in group.providers],
            })

        return {
            "groups": groups,
            "total": len(groups),
        }

    async def get_group(self, name: str) -> Optional[Dict[str, Any]]:
        """단일 그룹 조회."""
        running = list(self.manager.processes.keys())

        for group in self.manager.get_enabled_groups():
            if group.name == name:
                running_count = sum(1 for p in group.providers if p.name in running)
                return {
                    "name": group.name,
                    "order": group.order,
                    "enabled": group.enabled,
                    "provider_count": len(group.providers),
                    "running_count": running_count,
                    "providers": [p.name for p in group.providers],
                }
        return None

    # ==========================================
    # Status Operations
    # ==========================================

    async def get_status(self) -> Dict[str, Any]:
        """전체 상태 조회."""
        running = list(self.manager.processes.keys())
        groups = []

        for group in self.manager.get_enabled_groups():
            providers = [{
                "name": p.name,
                "port": p.port,
                "enabled": p.enabled,
                "running": p.name in running,
            } for p in group.providers]

            groups.append({
                "name": group.name,
                "order": group.order,
                "enabled": group.enabled,
                "providers": providers,
            })

        total_providers = sum(len(g["providers"]) for g in groups)

        # 작업 통계 추가
        job_stats = {}
        if self.job_tracker:
            job_stats = await self.job_tracker.get_all_stats()

        return {
            "groups": groups,
            "running_providers": running,
            "total_groups": len(groups),
            "total_providers": total_providers,
            "running_count": len(running),
            "jobs": job_stats,
        }

    async def start_all(self) -> Dict[str, Any]:
        """모든 프로바이더 시작."""
        await self.manager.start_all_providers()
        running = list(self.manager.processes.keys())
        return {
            "status": "started",
            "running_providers": running,
        }

    async def stop_all(self) -> Dict[str, Any]:
        """모든 프로바이더 중지."""
        await self.manager.stop_all_providers()
        return {
            "status": "stopped",
        }

    async def restart_all(self) -> Dict[str, Any]:
        """모든 프로바이더 재시작."""
        await self.manager.restart_all_providers()
        running = list(self.manager.processes.keys())
        return {
            "status": "restarted",
            "running_providers": running,
        }

    # ==========================================
    # Job Operations
    # ==========================================

    async def get_jobs(
        self,
        provider: str = None,
        trace_id: str = None,
    ) -> Dict[str, Any]:
        """작업 목록 조회."""
        if not self.job_tracker:
            return {"error": "JobTracker not initialized", "jobs": []}

        if trace_id:
            jobs = await self.job_tracker.get_jobs_by_trace_id(trace_id)
            return {
                "trace_id": trace_id,
                "total": len(jobs),
                "jobs": [self._job_to_dict(j) for j in jobs],
            }

        if provider:
            jobs = await self.job_tracker.get_provider_jobs(provider)
            return {
                "provider": provider,
                "active_jobs": len(jobs),
                "jobs": [self._job_to_dict(j) for j in jobs],
            }

        return await self.job_tracker.get_all_stats()

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """개별 작업 조회."""
        if not self.job_tracker:
            return None

        job = await self.job_tracker.get_job(job_id)
        if not job:
            return None

        return self._job_to_dict(job)

    def _job_to_dict(self, job) -> Dict[str, Any]:
        """JobInfo를 딕셔너리로 변환."""
        duration = None
        if job.completed_at:
            duration = job.completed_at - job.started_at
        elif job.status == JobStatus.PROCESSING:
            duration = time.time() - job.started_at

        return {
            "job_id": job.job_id,
            "request_id": job.request_id,
            "task_type": job.task_type,
            "provider": job.provider,
            "status": job.status.value,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "duration": duration,
            "error": job.error,
            "trace_id": job.trace_id,
        }

    # ==========================================
    # Process Inspection Operations
    # ==========================================

    def get_all_processes(self) -> Dict[str, Any]:
        """모든 프로바이더의 프로세스 정보 조회."""
        processes = self.manager.get_all_provider_processes()

        # 좀비 프로세스 카운트
        zombie_count = sum(1 for p in processes.values() if p.get("has_zombie"))
        total_processes = sum(p.get("process_count", 0) for p in processes.values())

        return {
            "providers": processes,
            "total_providers": len(processes),
            "total_processes": total_processes,
            "zombie_count": zombie_count,
        }

    def get_provider_processes(self, name: str) -> Optional[Dict[str, Any]]:
        """특정 프로바이더의 프로세스 정보 조회."""
        return self.manager.get_provider_processes(name)

    async def kill_provider_zombies(self, name: str) -> Dict[str, Any]:
        """특정 프로바이더의 좀비 프로세스 정리."""
        # 프로바이더 포트 찾기
        for group in self.manager.get_enabled_groups():
            for provider in group.providers:
                if provider.name == name:
                    killed = await self.manager.kill_zombie_on_port(provider.port)
                    return {
                        "provider": name,
                        "port": provider.port,
                        "killed_count": killed,
                    }
        return {"error": f"Provider not found: {name}"}

    async def kill_all_zombies(self) -> Dict[str, Any]:
        """모든 프로바이더의 좀비 프로세스 정리."""
        results = {}
        total_killed = 0

        for group in self.manager.get_enabled_groups():
            for provider in group.providers:
                killed = await self.manager.kill_zombie_on_port(provider.port)
                if killed > 0:
                    results[provider.name] = killed
                    total_killed += killed

        return {
            "total_killed": total_killed,
            "by_provider": results,
        }

    # ==========================================
    # Prometheus Metrics
    # ==========================================

    def get_prometheus_metrics(self) -> str:
        """Prometheus 형식의 메트릭 반환."""
        return self.manager.get_prometheus_metrics()
