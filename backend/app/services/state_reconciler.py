"""StateReconciler 서비스.

StateWatchdog가 발견한 문제를 해결합니다.
- Stuck 상태 파일 처리 (실패 마킹 또는 재시도)
- 작업 누락 복구 (재큐잉)
- 상태 불일치 수정
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import logger
from ..db.models import File, FileStatus, ContentType, SttLog, LlmLog, Content
from ..db.session import AsyncSessionLocal
from ..repositories.file_repository import FileRepository
from ..repositories.transcription_repository import TranscriptionRepository
from ..repositories.document_repository import DocumentRepository
from ..utils.task_queue_adapter import get_task_queue
from ..utils.event_publisher import publish_file_progress
from .state_watchdog import WatchdogFinding, run_watchdog_scan


@dataclass
class ReconcileAction:
    """Reconciler가 수행한 액션."""
    file_id: UUID
    action: str  # "marked_failed", "requeued", "fixed_status", "skipped"
    details: str
    success: bool


# 자동 재시도 가능한 issue 타입
RETRIABLE_ISSUES = ["stuck", "no_job"]

# 재시도 최대 횟수 (로그에서 카운트)
MAX_RETRY_COUNT = 3


class StateReconciler:
    """상태 불일치 해결 서비스.

    WatchdogFinding을 받아서 적절한 복구 조치를 수행합니다.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.file_repo = FileRepository(session)
        self.transcription_repo = TranscriptionRepository(session)
        self.document_repo = DocumentRepository(session)
        self.task_queue = get_task_queue()

    async def reconcile(self, findings: list[WatchdogFinding]) -> list[ReconcileAction]:
        """발견된 문제들을 처리합니다."""
        actions: list[ReconcileAction] = []

        for finding in findings:
            try:
                action = await self._handle_finding(finding)
                actions.append(action)
                await self.session.commit()
            except Exception as e:
                logger.error(f"[StateReconciler] Failed to handle finding for file {finding.file_id}: {e}")
                await self.session.rollback()
                actions.append(ReconcileAction(
                    file_id=finding.file_id,
                    action="error",
                    details=str(e),
                    success=False,
                ))

        return actions

    async def _handle_finding(self, finding: WatchdogFinding) -> ReconcileAction:
        """단일 문제를 처리합니다."""
        file = await self.file_repo.get_file(finding.file_id)
        if not file:
            return ReconcileAction(
                file_id=finding.file_id,
                action="skipped",
                details="File not found",
                success=False,
            )

        # 이미 완료/실패 상태면 스킵
        content = file.content
        if not content:
            return ReconcileAction(
                file_id=finding.file_id,
                action="skipped",
                details="Content not found for file",
                success=False,
            )

        status = content.status
        if status in [FileStatus.COMPLETED, FileStatus.CANCELLED,
                          FileStatus.ASR_FAILED, FileStatus.OCR_FAILED, FileStatus.SUMMARY_FAILED]:
            return ReconcileAction(
                file_id=finding.file_id,
                action="skipped",
                details=f"File already in terminal state: {status.value}",
                success=True,
            )

        # 재시도 횟수 확인
        retry_count = await self._get_retry_count(file.id)

        if finding.issue_type == "stuck":
            return await self._handle_stuck(file, status, finding, retry_count)
        elif finding.issue_type == "no_job":
            return await self._handle_no_job(file, status, finding, retry_count)
        elif finding.issue_type == "job_mismatch":
            return await self._handle_job_mismatch(file, status, finding)
        elif finding.issue_type == "job_failed":
            return await self._handle_job_failed(file, status, finding)
        else:
            return ReconcileAction(
                file_id=finding.file_id,
                action="skipped",
                details=f"Unknown issue type: {finding.issue_type}",
                success=False,
            )

    async def _handle_stuck(
        self, file: File, status: FileStatus, finding: WatchdogFinding, retry_count: int
    ) -> ReconcileAction:
        """Stuck 상태 처리."""
        if retry_count >= MAX_RETRY_COUNT:
            # 최대 재시도 횟수 초과 - 실패로 마킹
            return await self._mark_as_failed(
                file,
                status,
                f"Max retries ({MAX_RETRY_COUNT}) exceeded. Stuck for {finding.stuck_minutes:.1f} minutes.",
            )

        # 상태에 따라 재시도 또는 실패 처리
        if status == FileStatus.PROCESSING:
            # ASR 처리 중 stuck - 실패로 마킹 (ASR은 외부 서버 의존)
            return await self._mark_as_failed(
                file,
                status,
                f"ASR processing stuck for {finding.stuck_minutes:.1f} minutes",
            )

        elif status == FileStatus.OCR_PROCESSING:
            # OCR 처리 중 stuck - 실패로 마킹
            return await self._mark_as_failed(
                file,
                status,
                f"OCR processing stuck for {finding.stuck_minutes:.1f} minutes",
            )

        elif status == FileStatus.SUMMARIZING:
            # LLM 요약 중 stuck - 실패로 마킹
            return await self._mark_as_failed(
                file,
                status,
                f"LLM summarizing stuck for {finding.stuck_minutes:.1f} minutes",
            )

        elif status == FileStatus.SUMMARY_QUEUED:
            # 요약 대기 중 stuck - LLM 작업 재큐잉 시도
            return await self._requeue_llm_job(file, retry_count)

        elif status == FileStatus.QUEUED:
            # 초기 대기 중 stuck - 파일 타입에 따라 처리
            return await self._requeue_initial_job(file, retry_count)

        return ReconcileAction(
            file_id=file.id,
            action="skipped",
            details=f"No action defined for stuck {status.value}",
            success=False,
        )

    async def _handle_no_job(
        self, file: File, status: FileStatus, finding: WatchdogFinding, retry_count: int
    ) -> ReconcileAction:
        """작업이 없는 상태 처리."""
        if retry_count >= MAX_RETRY_COUNT:
            return await self._mark_as_failed(
                file,
                status,
                f"Max retries ({MAX_RETRY_COUNT}) exceeded. No job found.",
            )

        if status == FileStatus.SUMMARY_QUEUED:
            return await self._requeue_llm_job(file, retry_count)

        return ReconcileAction(
            file_id=file.id,
            action="skipped",
            details=f"No job handling for status {status.value}",
            success=False,
        )

    async def _handle_job_mismatch(
        self, file: File, status: FileStatus, finding: WatchdogFinding
    ) -> ReconcileAction:
        """작업 상태 불일치 처리.

        로그에는 실패/완료가 기록되어 있지만 DB 상태가 업데이트되지 않은 경우.
        """
        # 로그 분석하여 올바른 상태 결정
        last_event = finding.details

        if "_failed" in last_event:
            # 실패 이벤트가 있으면 실패 상태로 업데이트
            if status == FileStatus.PROCESSING:
                new_status = FileStatus.ASR_FAILED
            elif status == FileStatus.OCR_PROCESSING:
                new_status = FileStatus.OCR_FAILED
            elif status == FileStatus.SUMMARIZING:
                new_status = FileStatus.SUMMARY_FAILED
            else:
                new_status = FileStatus.CANCELLED

            await self.file_repo.update_file_status(file.id, new_status)
            await self.file_repo.add_log(
                file.id,
                log={"event": "reconciler_status_fix", "old_status": status.value, "new_status": new_status.value},
                message=f"[Reconciler] Fixed status: {status.value} → {new_status.value}",
            )

            # 이벤트 발행
            publish_file_progress(
                file_id=file.id,
                status="failed",
                step="reconciler",
                progress=0.0,
                message="처리 실패 (상태 복구됨)",
            )

            return ReconcileAction(
                file_id=file.id,
                action="fixed_status",
                details=f"Status updated: {status.value} → {new_status.value}",
                success=True,
            )

        return ReconcileAction(
            file_id=file.id,
            action="skipped",
            details="Could not determine correct status from mismatch",
            success=False,
        )

    async def _handle_job_failed(
        self, file: File, status: FileStatus, finding: WatchdogFinding
    ) -> ReconcileAction:
        """Celery 작업 실패 처리."""
        return await self._mark_as_failed(file, status, "Celery job failed")

    async def _mark_as_failed(self, file: File, status: FileStatus, reason: str) -> ReconcileAction:
        """파일을 실패 상태로 마킹합니다."""
        # 상태에 따라 적절한 실패 상태 결정
        if status in [FileStatus.QUEUED, FileStatus.PROCESSING]:
            new_status = FileStatus.ASR_FAILED
        elif status == FileStatus.OCR_PROCESSING:
            new_status = FileStatus.OCR_FAILED
        elif status in [FileStatus.SUMMARY_QUEUED, FileStatus.SUMMARIZING]:
            new_status = FileStatus.SUMMARY_FAILED
        else:
            new_status = FileStatus.CANCELLED

        await self.file_repo.update_file_status(file.id, new_status)
        await self.file_repo.add_log(
            file.id,
            log={"event": "reconciler_marked_failed", "reason": reason, "old_status": status.value},
            message=f"[Reconciler] Marked as failed: {reason}",
        )

        # 이벤트 발행
        publish_file_progress(
            file_id=file.id,
            status="failed",
            step="reconciler",
            progress=0.0,
            message=f"처리 실패: {reason}",
        )

        logger.info(f"[StateReconciler] Marked file {file.id} as {new_status.value}: {reason}")

        return ReconcileAction(
            file_id=file.id,
            action="marked_failed",
            details=f"Status: {status.value} → {new_status.value}. Reason: {reason}",
            success=True,
        )

    async def _requeue_llm_job(self, file: File, retry_count: int) -> ReconcileAction:
        """LLM 작업을 재큐잉합니다."""
        # 텍스트 소스 찾기 (Transcription 또는 Document)
        text_to_summarize = None

        if file.content_type == ContentType.AUDIO:
            transcription = await self.transcription_repo.get_by_file_id(file.id)
            if transcription and transcription.transcription:
                text_to_summarize = transcription.transcription.get("text", "")
        else:
            document = await self.document_repo.get_by_file_id(file.id)
            if document:
                text_to_summarize = document.ocr_text

        if not text_to_summarize:
            return await self._mark_as_failed(
                file,
                "Cannot requeue LLM job: no text found",
            )

        try:
            job_id = self.task_queue.enqueue_llm_job(
                file_id=file.id,
                text_to_summarize=text_to_summarize,
            )

            await self.file_repo.add_llm_log(
                file.id,
                log={"event": "reconciler_requeued", "job_id": job_id, "retry_count": retry_count + 1},
                message=f"[Reconciler] LLM job requeued (retry {retry_count + 1})",
            )

            logger.info(f"[StateReconciler] Requeued LLM job for file {file.id}: job_id={job_id}")

            return ReconcileAction(
                file_id=file.id,
                action="requeued",
                details=f"LLM job requeued: job_id={job_id} (retry {retry_count + 1})",
                success=True,
            )
        except Exception as e:
            logger.error(f"[StateReconciler] Failed to requeue LLM job for file {file.id}: {e}")
            return await self._mark_as_failed(file, f"Failed to requeue LLM job: {e}")

    async def _requeue_initial_job(self, file: File, retry_count: int) -> ReconcileAction:
        """초기 작업을 재큐잉합니다 (ASR 또는 OCR)."""
        # QUEUED 상태에서는 원본 파일로 재처리해야 하므로 현재는 실패 처리
        # (재큐잉하려면 원본 파라미터가 필요한데 저장하지 않음)
        return await self._mark_as_failed(
            file,
            f"QUEUED state stuck for too long. Cannot requeue initial job without original parameters.",
        )

    async def _get_retry_count(self, file_id: UUID) -> int:
        """Reconciler 재시도 횟수를 조회합니다."""
        from sqlalchemy import select, func

        # file_id로 content_id 조회
        content_stmt = select(Content.id).where(Content.file_id == file_id)
        content_result = await self.session.execute(content_stmt)
        content_id = content_result.scalar_one_or_none()

        if not content_id:
            return 0

        stmt = (
            select(func.count())
            .select_from(SttLog)
            .where(
                SttLog.content_id == content_id,
                SttLog.log["event"].astext == "reconciler_requeued",
            )
        )
        result = await self.session.execute(stmt)
        stt_count = result.scalar_one_or_none() or 0

        stmt = (
            select(func.count())
            .select_from(LlmLog)
            .where(
                LlmLog.content_id == content_id,
                LlmLog.log["event"].astext == "reconciler_requeued",
            )
        )
        result = await self.session.execute(stmt)
        llm_count = result.scalar_one_or_none() or 0

        return stt_count + llm_count


async def run_reconciler(findings: list[WatchdogFinding] | None = None) -> list[ReconcileAction]:
    """Reconciler를 실행합니다.

    Args:
        findings: WatchdogFinding 목록. None이면 watchdog 스캔부터 실행.

    Returns:
        수행된 ReconcileAction 목록
    """
    if findings is None:
        findings = await run_watchdog_scan()

    if not findings:
        logger.info("[StateReconciler] No issues to reconcile")
        return []

    async with AsyncSessionLocal() as session:
        reconciler = StateReconciler(session)
        actions = await reconciler.reconcile(findings)

        # 결과 로깅
        successful = sum(1 for a in actions if a.success)
        logger.info(f"[StateReconciler] Completed: {successful}/{len(actions)} actions successful")

        for action in actions:
            status = "✓" if action.success else "✗"
            logger.info(f"  {status} File {action.file_id}: {action.action} - {action.details}")

        return actions
