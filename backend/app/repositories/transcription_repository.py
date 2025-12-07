from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import models


class TranscriptionRepository:
    """전사 결과 CRUD 쿼리 집합."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_file_id(self, file_id: int) -> models.Transcription | None:
        """파일 ID로 전사 결과 조회."""
        stmt = select(models.Transcription).where(models.Transcription.file_id == file_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_transcription(
        self,
        *,
        file_id: int,
        speakers: list[str] | None = None,
        duration_seconds: float = 0.0,
        transcription: dict | None = None,
    ) -> models.Transcription:
        """전사 결과 생성."""
        transcription_obj = models.Transcription(
            file_id=file_id,
            speakers=speakers or [],
            duration_seconds=duration_seconds,
            transcription=transcription or {},
        )
        self.session.add(transcription_obj)
        await self.session.flush()
        return transcription_obj

    async def update_transcription(
        self,
        file_id: int,
        *,
        speakers: list[str],
        duration_seconds: float,
        transcription: dict,
    ) -> models.Transcription:
        """전사 결과 업데이트."""
        transcription_obj = await self.get_by_file_id(file_id)
        if not transcription_obj:
            raise ValueError("Transcription not found")
        
        transcription_obj.speakers = speakers
        transcription_obj.duration_seconds = duration_seconds
        transcription_obj.transcription = transcription
        await self.session.flush()
        return transcription_obj

