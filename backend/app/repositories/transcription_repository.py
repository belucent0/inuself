from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import models


class TranscriptionRepository:
    """전사 결과 CRUD 쿼리 집합."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_file_id(self, file_id: UUID) -> models.Transcription | None:
        """파일 ID (UUID)로 전사 결과 조회 (content_id 기준)."""
        # file_id -> content_id 변환 후 조회
        content_id = await self._get_content_id_from_file_id(file_id)
        if not content_id:
            return None
        return await self.get_by_content_id(content_id)

    async def get_by_content_id(self, content_id: UUID) -> models.Transcription | None:
        """Content ID (UUID)로 전사 결과 조회."""
        stmt = select(models.Transcription).where(models.Transcription.content_id == content_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_content_id_from_file_id(self, file_id: UUID) -> UUID | None:
        """File ID (UUID)로 Content ID (UUID) 조회."""
        stmt = select(models.Content.id).where(models.Content.file_id == file_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_transcription(
        self,
        *,
        file_id: UUID,
        speakers: list[str] | None = None,
        duration_seconds: float = 0.0,
        transcription: dict | None = None,
    ) -> models.Transcription:
        """전사 결과 생성 (content_id 기준)."""
        # Content ID 조회
        content_id = await self._get_content_id_from_file_id(file_id)
        if not content_id:
            raise ValueError(f"Content not found for file_id={file_id}")

        transcription_obj = models.Transcription(
            content_id=content_id,
            speakers=speakers or [],
            duration_seconds=duration_seconds,
            transcription=transcription or {},
        )
        self.session.add(transcription_obj)
        await self.session.flush()
        return transcription_obj

    async def update_transcription(
        self,
        file_id: UUID,
        *,
        speakers: list[str],
        duration_seconds: float,
        transcription: dict,
    ) -> models.Transcription:
        """전사 결과 업데이트 (file_id -> content_id 변환)."""
        # file_id -> content_id 변환
        content_id = await self._get_content_id_from_file_id(file_id)
        if not content_id:
            raise ValueError(f"Content not found for file_id={file_id}")

        return await self.update_transcription_by_content_id(
            content_id,
            speakers=speakers,
            duration_seconds=duration_seconds,
            transcription=transcription,
        )

    async def update_transcription_by_content_id(
        self,
        content_id: UUID,
        *,
        speakers: list[str],
        duration_seconds: float,
        transcription: dict,
    ) -> models.Transcription:
        """전사 결과 업데이트 (content_id UUID 기준)."""
        transcription_obj = await self.get_by_content_id(content_id)
        if not transcription_obj:
            raise ValueError("Transcription not found")

        transcription_obj.speakers = speakers
        transcription_obj.duration_seconds = duration_seconds
        transcription_obj.transcription = transcription
        await self.session.flush()
        return transcription_obj

    async def update_transcription_jsonb(
        self,
        file_id: UUID,
        transcription: dict,
    ) -> None:
        """transcription JSONB 컬럼만 부분 update (번역 점진 저장용).

        speakers/duration_seconds는 건드리지 않는다. SQLAlchemy ORM의
        JSONB mutable 추적이 segment 단위 수정에는 트리거되지 않으므로
        매번 dict 전체를 새 객체로 재할당한다.
        """
        content_id = await self._get_content_id_from_file_id(file_id)
        if not content_id:
            raise ValueError(f"Content not found for file_id={file_id}")
        transcription_obj = await self.get_by_content_id(content_id)
        if not transcription_obj:
            raise ValueError("Transcription not found")
        # 새 dict 객체로 재할당 — SQLAlchemy가 변경 감지
        transcription_obj.transcription = dict(transcription)
        await self.session.flush()
