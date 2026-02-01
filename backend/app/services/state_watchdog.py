"""StateWatchdog 서비스.

파일 처리 상태의 불일치 및 stuck 상태를 감지합니다.
주기적으로 실행되어 PROCESSING, SUMMARIZING 등 진행 상태의 파일을 모니터링합니다.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Sequence
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import logger
from ..db.models import File, FileStatus, SttLog, LlmLog, Content
from ..db.session import AsyncSessionLocal
from ..repositories.file_repository import FileRepository
from ..utils.task_queue_adapter import get_task_queue


@dataclass
class WatchdogFinding:
    """Watchdog이 발견한 문제."""

    file_id: UUID
    file_status: FileStatus
    issue_type: str  # "stuck", "no_job", "job_failed", "job_mismatch"
    details: str
    celery_status: str | None = None
    stuck_minutes: float | None = None


# 상태별 타임아웃 (분)
STATUS_TIMEOUTS = {
    FileStatus.QUEUED: 30,  # 30분 동안 QUEUED 상태면 문제
    FileStatus.PULLING: 30,  # 외부 소스 다운로드 30분 타임아웃
    FileStatus.PROCESSING: 60,  # ASR 처리 60분 타임아웃
    FileStatus.OCR_PROCESSING: 30,  # OCR 처리 30분 타임아웃
    FileStatus.SUMMARY_QUEUED: 30,  # 요약 대기 30분 타임아웃
    FileStatus.SUMMARIZING: 15,  # LLM 요약 15분 타임아웃
}

# 모니터링 대상 상태
UNSTABLE_STATUSES = [
    FileStatus.QUEUED,
    FileStatus.PULLING,
    FileStatus.PROCESSING,
    FileStatus.OCR_PROCESSING,
    FileStatus.SUMMARY_QUEUED,
    FileStatus.SUMMARIZING,
]


class StateWatchdog:
    """파일 처리 상태 모니터링 서비스.

    주기적으로 실행되어:
    1. 타임아웃된 stuck 상태 감지
    2. Celery 작업 상태와 DB 상태 불일치 감지
    3. 문제가 있는 파일 목록을 StateReconciler에 전달
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.file_repo = FileRepository(session)
        self.task_queue = get_task_queue()

    async def scan_all(self) -> list[WatchdogFinding]:
        """모든 불안정 상태의 파일을 스캔하여 문제를 탐지합니다."""
        findings: list[WatchdogFinding] = []

        # 불안정 상태의 파일 조회
        files = await self._get_unstable_files()

        if not files:
            logger.debug("[StateWatchdog] No unstable files found")
            return findings

        logger.info(f"[StateWatchdog] Scanning {len(files)} unstable files")

        for file in files:
            file_findings = await self._check_file(file)
            findings.extend(file_findings)

        if findings:
            logger.warning(f"[StateWatchdog] Found {len(findings)} issues")
            for f in findings:
                logger.warning(f"  - File {f.file_id}: {f.issue_type} - {f.details}")

        return findings

    async def _get_unstable_files(self) -> Sequence[File]:
        """불안정 상태의 파일 목록을 조회합니다."""
        stmt = (
            select(File)
            .join(Content, Content.file_id == File.id)
            .options(selectinload(File.content))
            .where(Content.status.in_(UNSTABLE_STATUSES))
            .order_by(File.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def _check_file(self, file: File) -> list[WatchdogFinding]:
        """단일 파일의 상태를 검사합니다."""
        findings: list[WatchdogFinding] = []
        now = datetime.now(timezone.utc)

        # Content에서 status 조회
        content = file.content
        if not content:
            return findings

        status = content.status

        # 1. 타임아웃 체크
        timeout_minutes = STATUS_TIMEOUTS.get(status, 30)
        age_minutes = (now - file.created_at).total_seconds() / 60

        # 마지막 로그 시간으로 stuck 판단 (최근 활동 기준)
        last_activity = await self._get_last_activity_time(file.id)
        if last_activity:
            stuck_minutes = (now - last_activity).total_seconds() / 60
        else:
            stuck_minutes = age_minutes

        if stuck_minutes > timeout_minutes:
            findings.append(
                WatchdogFinding(
                    file_id=file.id,
                    file_status=status,
                    issue_type="stuck",
                    details=f"Status '{status.value}' for {stuck_minutes:.1f} minutes (timeout: {timeout_minutes}m)",
                    stuck_minutes=stuck_minutes,
                )
            )

        # 2. Celery 작업 상태 확인 (PROCESSING/SUMMARIZING/OCR_PROCESSING 상태)
        if status in [
            FileStatus.PROCESSING,
            FileStatus.SUMMARIZING,
            FileStatus.OCR_PROCESSING,
        ]:
            celery_finding = await self._check_celery_job(file, status)
            if celery_finding:
                findings.append(celery_finding)

        # 3. SUMMARY_QUEUED 상태에서 LLM 작업이 큐에 있는지 확인
        if status == FileStatus.SUMMARY_QUEUED and stuck_minutes > 5:
            # 5분 이상 SUMMARY_QUEUED면 작업이 큐잉되었는지 확인
            has_llm_job = await self._check_llm_job_queued(file.id)
            if not has_llm_job:
                findings.append(
                    WatchdogFinding(
                        file_id=file.id,
                        file_status=status,
                        issue_type="no_job",
                        details=f"SUMMARY_QUEUED but no LLM job found after {stuck_minutes:.1f} minutes",
                        stuck_minutes=stuck_minutes,
                    )
                )

        return findings

    async def _get_last_activity_time(self, file_id: UUID) -> datetime | None:
        """파일의 마지막 활동 시간을 조회합니다 (로그 기준)."""
        # file_id로 content_id 조회
        content_stmt = select(Content.id).where(Content.file_id == file_id)
        content_result = await self.session.execute(content_stmt)
        content_id = content_result.scalar_one_or_none()

        if not content_id:
            return None

        # SttLog에서 가장 최근 로그 시간
        stt_stmt = (
            select(SttLog.created_at)
            .where(SttLog.content_id == content_id)
            .order_by(SttLog.created_at.desc())
            .limit(1)
        )
        stt_result = await self.session.execute(stt_stmt)
        stt_time = stt_result.scalar_one_or_none()

        # LlmLog에서 가장 최근 로그 시간
        llm_stmt = (
            select(LlmLog.created_at)
            .where(LlmLog.content_id == content_id)
            .order_by(LlmLog.created_at.desc())
            .limit(1)
        )
        llm_result = await self.session.execute(llm_stmt)
        llm_time = llm_result.scalar_one_or_none()

        # 둘 중 더 최근 시간 반환
        times = [t for t in [stt_time, llm_time] if t is not None]
        if times:
            return max(times)
        return None

    async def _check_celery_job(
        self, file: File, status: FileStatus
    ) -> WatchdogFinding | None:
        """Celery 작업 상태를 확인합니다.

        Note: 현재 구조에서는 job_id를 별도로 저장하지 않으므로,
        로그에서 job_id를 추출하거나 Redis를 직접 조회해야 합니다.
        여기서는 로그 기반으로 판단합니다.
        """
        # 현재 아키텍처에서는 Celery job_id를 File에 저장하지 않음
        # 로그에서 마지막 이벤트를 확인하여 상태 판단
        last_log = await self._get_last_log_event(file.id)

        if last_log:
            event = last_log.get("event", "")

            # 시작 이벤트 이후 완료/실패 이벤트가 없으면 작업이 진행 중이거나 stuck
            if event.endswith("_started"):
                # 시작 후 진행 중으로 판단 (stuck 체크는 타임아웃에서 처리)
                return None
            elif event.endswith("_failed"):
                # 이미 실패 처리된 것으로 보이지만 상태가 아직 안 바뀜
                return WatchdogFinding(
                    file_id=file.id,
                    file_status=status,
                    issue_type="job_mismatch",
                    details=f"Last event was '{event}' but status is still {status.value}",
                )

        return None

    async def _get_last_log_event(self, file_id: UUID) -> dict | None:
        """파일의 마지막 로그 이벤트를 조회합니다."""
        # file_id로 content_id 조회
        content_stmt = select(Content.id).where(Content.file_id == file_id)
        content_result = await self.session.execute(content_stmt)
        content_id = content_result.scalar_one_or_none()

        if not content_id:
            return None

        # SttLog에서 조회
        stt_stmt = (
            select(SttLog)
            .where(SttLog.content_id == content_id)
            .order_by(SttLog.created_at.desc())
            .limit(1)
        )
        stt_result = await self.session.execute(stt_stmt)
        stt_log = stt_result.scalar_one_or_none()

        # LlmLog에서 조회
        llm_stmt = (
            select(LlmLog)
            .where(LlmLog.content_id == content_id)
            .order_by(LlmLog.created_at.desc())
            .limit(1)
        )
        llm_result = await self.session.execute(llm_stmt)
        llm_log = llm_result.scalar_one_or_none()

        # 더 최근 로그 반환
        if stt_log and llm_log:
            if stt_log.created_at > llm_log.created_at:
                return stt_log.log
            return llm_log.log
        elif stt_log:
            return stt_log.log
        elif llm_log:
            return llm_log.log

        return None

    async def _check_llm_job_queued(self, file_id: UUID) -> bool:
        """LLM 작업이 큐에 있는지 확인합니다.

        LlmLog에 큐잉 로그가 있는지 확인합니다.
        """
        # file_id로 content_id 조회
        content_stmt = select(Content.id).where(Content.file_id == file_id)
        content_result = await self.session.execute(content_stmt)
        content_id = content_result.scalar_one_or_none()

        if not content_id:
            return False

        stmt = (
            select(LlmLog)
            .where(
                and_(
                    LlmLog.content_id == content_id,
                    # 큐잉 관련 로그 확인 (ASR/OCR 완료 시 자동 큐잉됨)
                )
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def run_watchdog_scan() -> list[WatchdogFinding]:
    """Watchdog 스캔을 실행합니다 (독립 실행용)."""
    async with AsyncSessionLocal() as session:
        watchdog = StateWatchdog(session)
        return await watchdog.scan_all()
