"""범용 검사 결과 Repository."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ScanResult


class ScanRepository:
    """범용 검사 결과 데이터 접근 레이어."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, result_id: UUID) -> ScanResult | None:
        """검사 결과 ID로 조회."""
        stmt = select(ScanResult).where(ScanResult.id == result_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_by_user(
        self, user_id: UUID, scan_type: str, status: str | None = None
    ) -> ScanResult | None:
        """사용자의 특정 검사 유형 최신 결과 조회."""
        stmt = (
            select(ScanResult)
            .where(ScanResult.user_id == user_id, ScanResult.scan_type == scan_type)
        )
        if status:
            stmt = stmt.where(ScanResult.status == status)
        stmt = stmt.order_by(desc(ScanResult.created_at)).limit(1)

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_in_progress(self, user_id: UUID, scan_type: str) -> ScanResult | None:
        """사용자의 진행 중인 검사 조회."""
        return await self.get_latest_by_user(user_id, scan_type, status="in_progress")

    async def get_history(
        self,
        user_id: UUID,
        scan_type: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ScanResult]:
        """검사 이력 조회."""
        stmt = select(ScanResult).where(ScanResult.user_id == user_id)

        if scan_type:
            stmt = stmt.where(ScanResult.scan_type == scan_type)
        if status:
            stmt = stmt.where(ScanResult.status == status)

        stmt = stmt.order_by(desc(ScanResult.created_at)).offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        user_id: UUID,
        scan_type: str | None = None,
        status: str | None = None,
    ) -> int:
        """검사 이력 개수."""
        stmt = select(func.count(ScanResult.id)).where(ScanResult.user_id == user_id)

        if scan_type:
            stmt = stmt.where(ScanResult.scan_type == scan_type)
        if status:
            stmt = stmt.where(ScanResult.status == status)

        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def create(
        self,
        user_id: UUID,
        scan_type: str,
        data: dict,
        status: str = "in_progress",
    ) -> ScanResult:
        """새 검사 결과 생성."""
        scan_result = ScanResult(
            user_id=user_id,
            scan_type=scan_type,
            status=status,
            data=data,
        )
        self.session.add(scan_result)
        await self.session.flush()
        return scan_result

    async def update(
        self,
        scan_result: ScanResult,
        data: dict | None = None,
        status: str | None = None,
    ) -> ScanResult:
        """검사 결과 업데이트."""
        if data is not None:
            scan_result.data = data
        if status is not None:
            scan_result.status = status
        scan_result.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return scan_result

    async def delete(self, scan_result: ScanResult) -> None:
        """검사 결과 삭제."""
        await self.session.delete(scan_result)
        await self.session.flush()
