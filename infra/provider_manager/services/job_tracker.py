"""Job Tracker - 프로바이더별 작업 추적 서비스.

Redis Hash를 사용하여 작업 상태를 추적합니다.
- job:{job_id} - 작업 정보 (시작시간, 상태, 종료시간, 프로바이더 등)
- provider:{name}:jobs - 프로바이더별 활성 작업 Set
"""

import json
import time
import logging
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict
from enum import Enum

import redis.asyncio as redis_async

from core.config import settings

logger = logging.getLogger("JobTracker")


class JobStatus(str, Enum):
    """작업 상태."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class JobInfo:
    """작업 정보."""
    job_id: str
    request_id: str
    task_type: str
    provider: str
    status: JobStatus
    started_at: float
    completed_at: Optional[float] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    trace_id: Optional[str] = None  # 클라이언트에서 전달된 TraceId

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        data = asdict(self)
        data["status"] = self.status.value
        if self.metadata:
            data["metadata"] = json.dumps(self.metadata)
        # Remove None values for Redis compatibility
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobInfo":
        """딕셔너리에서 생성."""
        if "metadata" in data and isinstance(data["metadata"], str):
            try:
                data["metadata"] = json.loads(data["metadata"])
            except json.JSONDecodeError:
                data["metadata"] = None
        data["status"] = JobStatus(data["status"])
        if data.get("completed_at"):
            data["completed_at"] = float(data["completed_at"])
        if data.get("started_at"):
            data["started_at"] = float(data["started_at"])
        return cls(**data)


class JobTracker:
    """프로바이더별 작업 추적 서비스.

    Redis Hash를 사용하여 작업 상태를 추적합니다.
    """

    # Redis key prefixes
    JOB_KEY_PREFIX = "job:"
    PROVIDER_JOBS_PREFIX = "provider:jobs:"
    ALL_JOBS_KEY = "jobs:active"

    # 작업 정보 TTL (24시간)
    JOB_TTL = 86400

    def __init__(self, redis_client: redis_async.Redis = None):
        self.redis = redis_client

    async def connect(self, redis_url: str = None):
        """Redis 연결."""
        if not self.redis:
            url = redis_url or settings.redis_url
            self.redis = redis_async.from_url(url, decode_responses=True)
        logger.info("JobTracker connected to Redis")

    async def close(self):
        """리소스 정리."""
        if self.redis:
            await self.redis.aclose()

    def _job_key(self, job_id: str) -> str:
        """작업 키 생성."""
        return f"{self.JOB_KEY_PREFIX}{job_id}"

    def _provider_jobs_key(self, provider: str) -> str:
        """프로바이더 작업 Set 키 생성."""
        return f"{self.PROVIDER_JOBS_PREFIX}{provider}"

    async def start_job(
        self,
        job_id: str,
        request_id: str,
        task_type: str,
        provider: str,
        metadata: Dict[str, Any] = None,
        trace_id: str = None,
    ) -> JobInfo:
        """작업 시작 기록.

        Args:
            job_id: 작업 ID (메시지 ID 사용)
            request_id: 요청 ID
            task_type: 작업 유형 (diarization, transcription, llm_completion, ocr)
            provider: 처리 프로바이더 이름
            metadata: 추가 메타데이터
            trace_id: 클라이언트에서 전달된 TraceId (분산 추적용)

        Returns:
            생성된 JobInfo
        """
        job = JobInfo(
            job_id=job_id,
            request_id=request_id,
            task_type=task_type,
            provider=provider,
            status=JobStatus.PROCESSING,
            started_at=time.time(),
            metadata=metadata,
            trace_id=trace_id,
        )

        # Redis에 저장
        job_key = self._job_key(job_id)
        await self.redis.hset(job_key, mapping=job.to_dict())
        await self.redis.expire(job_key, self.JOB_TTL)

        # 프로바이더별 활성 작업에 추가
        provider_key = self._provider_jobs_key(provider)
        await self.redis.sadd(provider_key, job_id)

        # 전체 활성 작업에 추가
        await self.redis.sadd(self.ALL_JOBS_KEY, job_id)

        # TraceId 인덱스 추가 (분산 추적용)
        if trace_id:
            trace_key = f"trace:{trace_id}:jobs"
            await self.redis.sadd(trace_key, job_id)
            await self.redis.expire(trace_key, self.JOB_TTL)

        log_trace = f" (trace_id={trace_id})" if trace_id else ""
        logger.info(f"Job started: {job_id} ({task_type}) on {provider}{log_trace}")
        return job

    async def complete_job(
        self,
        job_id: str,
        success: bool = True,
        error: str = None,
    ) -> Optional[JobInfo]:
        """작업 완료 기록.

        Args:
            job_id: 작업 ID
            success: 성공 여부
            error: 에러 메시지 (실패 시)

        Returns:
            업데이트된 JobInfo 또는 None
        """
        job_key = self._job_key(job_id)

        # 작업 정보 가져오기
        data = await self.redis.hgetall(job_key)
        if not data:
            logger.warning(f"Job not found: {job_id}")
            return None

        job = JobInfo.from_dict(data)
        provider = job.provider

        # 상태 업데이트
        job.status = JobStatus.COMPLETED if success else JobStatus.FAILED
        job.completed_at = time.time()
        if error:
            job.error = error

        # Redis 업데이트
        await self.redis.hset(job_key, mapping=job.to_dict())

        # 활성 작업에서 제거
        provider_key = self._provider_jobs_key(provider)
        await self.redis.srem(provider_key, job_id)
        await self.redis.srem(self.ALL_JOBS_KEY, job_id)

        duration = job.completed_at - job.started_at
        status_str = "completed" if success else f"failed: {error}"
        logger.info(f"Job {status_str}: {job_id} ({duration:.2f}s)")

        return job

    async def get_job(self, job_id: str) -> Optional[JobInfo]:
        """작업 정보 조회.

        Args:
            job_id: 작업 ID

        Returns:
            JobInfo 또는 None
        """
        job_key = self._job_key(job_id)
        data = await self.redis.hgetall(job_key)
        if not data:
            return None
        return JobInfo.from_dict(data)

    async def get_provider_jobs(self, provider: str) -> List[JobInfo]:
        """프로바이더의 활성 작업 목록 조회.

        Args:
            provider: 프로바이더 이름

        Returns:
            활성 작업 목록
        """
        provider_key = self._provider_jobs_key(provider)
        job_ids = await self.redis.smembers(provider_key)

        jobs = []
        for job_id in job_ids:
            job = await self.get_job(job_id)
            if job:
                jobs.append(job)

        return jobs

    async def get_jobs_by_trace_id(self, trace_id: str) -> List[JobInfo]:
        """TraceId로 작업 목록 조회.

        분산 추적 시 특정 요청 체인의 모든 작업을 조회합니다.

        Args:
            trace_id: 클라이언트 TraceId

        Returns:
            해당 TraceId의 작업 목록
        """
        trace_key = f"trace:{trace_id}:jobs"
        job_ids = await self.redis.smembers(trace_key)

        jobs = []
        for job_id in job_ids:
            job = await self.get_job(job_id)
            if job:
                jobs.append(job)

        return sorted(jobs, key=lambda j: j.started_at)

    async def get_all_active_jobs(self) -> List[JobInfo]:
        """모든 활성 작업 목록 조회.

        Returns:
            활성 작업 목록
        """
        job_ids = await self.redis.smembers(self.ALL_JOBS_KEY)

        jobs = []
        for job_id in job_ids:
            job = await self.get_job(job_id)
            if job:
                jobs.append(job)

        return sorted(jobs, key=lambda j: j.started_at, reverse=True)

    async def get_jobs_by_status(
        self,
        status: JobStatus = None,
        provider: str = None,
        limit: int = 100,
    ) -> List[JobInfo]:
        """상태별/프로바이더별 작업 조회.

        Args:
            status: 필터링할 상태 (None이면 모든 상태)
            provider: 필터링할 프로바이더 (None이면 모든 프로바이더)
            limit: 최대 개수

        Returns:
            작업 목록
        """
        # 활성 작업만 조회 (완료된 작업은 TTL 후 자동 삭제)
        if provider:
            provider_key = self._provider_jobs_key(provider)
            job_ids = await self.redis.smembers(provider_key)
        else:
            job_ids = await self.redis.smembers(self.ALL_JOBS_KEY)

        jobs = []
        for job_id in list(job_ids)[:limit]:
            job = await self.get_job(job_id)
            if job:
                if status is None or job.status == status:
                    jobs.append(job)

        return sorted(jobs, key=lambda j: j.started_at, reverse=True)

    async def get_provider_stats(self, provider: str) -> Dict[str, Any]:
        """프로바이더 통계 조회.

        Args:
            provider: 프로바이더 이름

        Returns:
            통계 정보
        """
        jobs = await self.get_provider_jobs(provider)

        return {
            "provider": provider,
            "active_jobs": len(jobs),
            "jobs": [
                {
                    "job_id": j.job_id,
                    "task_type": j.task_type,
                    "status": j.status.value,
                    "started_at": j.started_at,
                    "duration": time.time() - j.started_at,
                }
                for j in jobs
            ],
        }

    async def get_all_stats(self) -> Dict[str, Any]:
        """전체 통계 조회.

        Returns:
            전체 통계 정보
        """
        all_jobs = await self.get_all_active_jobs()

        # 프로바이더별 그룹화
        by_provider = {}
        by_type = {}

        for job in all_jobs:
            # 프로바이더별
            if job.provider not in by_provider:
                by_provider[job.provider] = []
            by_provider[job.provider].append(job)

            # 타입별
            if job.task_type not in by_type:
                by_type[job.task_type] = 0
            by_type[job.task_type] += 1

        return {
            "total_active": len(all_jobs),
            "by_provider": {
                p: len(jobs) for p, jobs in by_provider.items()
            },
            "by_type": by_type,
            "jobs": [
                {
                    "job_id": j.job_id,
                    "request_id": j.request_id,
                    "task_type": j.task_type,
                    "provider": j.provider,
                    "status": j.status.value,
                    "started_at": j.started_at,
                    "duration": time.time() - j.started_at,
                }
                for j in all_jobs
            ],
        }

    async def cleanup_stale_jobs(self, max_age: float = 3600) -> int:
        """오래된 활성 작업 정리.

        Args:
            max_age: 최대 허용 시간 (초)

        Returns:
            정리된 작업 수
        """
        cleaned = 0
        all_jobs = await self.get_all_active_jobs()
        now = time.time()

        for job in all_jobs:
            if now - job.started_at > max_age:
                await self.complete_job(
                    job.job_id,
                    success=False,
                    error=f"Timeout after {max_age}s",
                )
                cleaned += 1

        if cleaned:
            logger.info(f"Cleaned up {cleaned} stale jobs")

        return cleaned
