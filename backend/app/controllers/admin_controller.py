"""관리자 API 컨트롤러.

시스템 관리 기능 (Watchdog, Reconciler 등) 엔드포인트.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.auth import require_admin
from ..db.session import get_session
from ..services.state_watchdog import StateWatchdog, WatchdogFinding, run_watchdog_scan
from ..services.state_reconciler import StateReconciler, ReconcileAction, run_reconciler

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


class WatchdogFindingResponse(BaseModel):
    """Watchdog 발견 항목 응답."""
    file_id: int
    file_status: str
    issue_type: str
    details: str
    celery_status: str | None = None
    stuck_minutes: float | None = None


class WatchdogScanResponse(BaseModel):
    """Watchdog 스캔 결과 응답."""
    findings: list[WatchdogFindingResponse]
    total_issues: int


class ReconcileActionResponse(BaseModel):
    """Reconciler 액션 응답."""
    file_id: int
    action: str
    details: str
    success: bool


class ReconcileResponse(BaseModel):
    """Reconciler 실행 결과 응답."""
    actions: list[ReconcileActionResponse]
    successful: int
    failed: int


@router.get("/watchdog/scan", response_model=WatchdogScanResponse)
async def scan_watchdog(session: AsyncSession = Depends(get_session)):
    """불안정 상태의 파일을 스캔하여 문제를 탐지합니다.

    주기적으로 자동 실행되지만, 수동으로도 호출할 수 있습니다.
    실제 상태 변경은 하지 않고 문제만 탐지합니다.
    """
    watchdog = StateWatchdog(session)
    findings = await watchdog.scan_all()

    return WatchdogScanResponse(
        findings=[
            WatchdogFindingResponse(
                file_id=f.file_id,
                file_status=f.file_status.value,
                issue_type=f.issue_type,
                details=f.details,
                celery_status=f.celery_status,
                stuck_minutes=f.stuck_minutes,
            )
            for f in findings
        ],
        total_issues=len(findings),
    )


@router.post("/watchdog/reconcile", response_model=ReconcileResponse)
async def run_reconcile(
    dry_run: bool = Query(False, description="True면 실제 변경 없이 예상 액션만 반환"),
    session: AsyncSession = Depends(get_session),
):
    """Watchdog이 발견한 문제를 해결합니다.

    1. 불안정 상태 파일 스캔
    2. 문제 유형에 따라 복구 조치 수행
       - stuck 상태: 실패 마킹 또는 재큐잉
       - 작업 누락: 재큐잉 시도
       - 상태 불일치: 상태 수정

    Args:
        dry_run: True면 실제 변경 없이 어떤 액션이 수행될지만 반환
    """
    if dry_run:
        # dry_run 모드에서는 스캔만 수행
        watchdog = StateWatchdog(session)
        findings = await watchdog.scan_all()

        # 예상 액션 생성 (실제 실행 없이)
        actions = []
        for f in findings:
            expected_action = _predict_action(f)
            actions.append(ReconcileActionResponse(
                file_id=f.file_id,
                action=expected_action,
                details=f"[DRY RUN] Would {expected_action}: {f.details}",
                success=True,
            ))

        return ReconcileResponse(
            actions=actions,
            successful=len(actions),
            failed=0,
        )

    # 실제 reconcile 실행
    watchdog = StateWatchdog(session)
    findings = await watchdog.scan_all()

    if not findings:
        return ReconcileResponse(actions=[], successful=0, failed=0)

    reconciler = StateReconciler(session)
    actions = await reconciler.reconcile(findings)

    successful = sum(1 for a in actions if a.success)
    failed = len(actions) - successful

    return ReconcileResponse(
        actions=[
            ReconcileActionResponse(
                file_id=a.file_id,
                action=a.action,
                details=a.details,
                success=a.success,
            )
            for a in actions
        ],
        successful=successful,
        failed=failed,
    )


def _predict_action(finding: WatchdogFinding) -> str:
    """Finding에 대해 예상되는 액션을 예측합니다."""
    if finding.issue_type == "stuck":
        if finding.file_status.value in ["PROCESSING", "OCR_PROCESSING", "SUMMARIZING"]:
            return "mark_failed"
        elif finding.file_status.value == "SUMMARY_QUEUED":
            return "requeue_llm"
        else:
            return "mark_failed"
    elif finding.issue_type == "no_job":
        return "requeue"
    elif finding.issue_type == "job_mismatch":
        return "fix_status"
    elif finding.issue_type == "job_failed":
        return "mark_failed"
    return "unknown"
