from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import models


class DocumentRepository:
    """문서 OCR 결과 CRUD 쿼리 집합."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_file_id(self, file_id: UUID) -> models.Document | None:
        """파일 ID (UUID)로 문서 조회 (content_id 기준)."""
        # file_id -> content_id 변환 후 조회
        content_id = await self._get_content_id_from_file_id(file_id)
        if not content_id:
            return None
        return await self.get_by_content_id(content_id)

    async def get_by_content_id(self, content_id: UUID) -> models.Document | None:
        """Content ID (UUID)로 문서 조회."""
        stmt = select(models.Document).where(models.Document.content_id == content_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_content_id_from_file_id(self, file_id: UUID) -> UUID | None:
        """File ID (UUID)로 Content ID (UUID) 조회."""
        stmt = select(models.Content.id).where(models.Content.file_id == file_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_document(
        self,
        *,
        file_id: UUID,
        ocr_text: str = "",
        page_count: int = 0,
        ocr_metadata: dict | None = None,
        html_content: str | None = None,
    ) -> models.Document:
        """문서 생성 (content_id 기준)."""
        # Content ID 조회
        content_id = await self._get_content_id_from_file_id(file_id)
        if not content_id:
            raise ValueError(f"Content not found for file_id={file_id}")

        document_obj = models.Document(
            content_id=content_id,
            ocr_text=ocr_text,
            page_count=page_count,
            ocr_metadata=ocr_metadata or {},
            html_content=html_content,
        )
        self.session.add(document_obj)
        await self.session.flush()
        return document_obj

    async def update_document(
        self,
        file_id: UUID,
        *,
        ocr_text: str,
        page_count: int,
        ocr_metadata: dict | None = None,
        html_content: str | None = None,
    ) -> models.Document:
        """문서 업데이트."""
        document_obj = await self.get_by_file_id(file_id)
        if not document_obj:
            raise ValueError("Document not found")

        document_obj.ocr_text = ocr_text
        document_obj.page_count = page_count
        if ocr_metadata is not None:
            document_obj.ocr_metadata = ocr_metadata
        if html_content is not None:
            document_obj.html_content = html_content
        await self.session.flush()
        return document_obj
