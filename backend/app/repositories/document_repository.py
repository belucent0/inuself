from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import models


class DocumentRepository:
    """문서 OCR 결과 CRUD 쿼리 집합."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_file_id(self, file_id: int) -> models.Document | None:
        """파일 ID로 문서 조회."""
        stmt = select(models.Document).where(models.Document.file_id == file_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_document(
        self,
        *,
        file_id: int,
        ocr_text: str = "",
        page_count: int = 0,
        ocr_metadata: dict | None = None,
    ) -> models.Document:
        """문서 생성."""
        document = models.Document(
            file_id=file_id,
            ocr_text=ocr_text,
            page_count=page_count,
            ocr_metadata=ocr_metadata or {},
        )
        self.session.add(document)
        await self.session.flush()
        return document

    async def update_document(
        self,
        file_id: int,
        *,
        ocr_text: str,
        page_count: int,
        ocr_metadata: dict | None = None,
    ) -> models.Document:
        """문서 업데이트."""
        document = await self.get_by_file_id(file_id)
        if not document:
            raise ValueError("Document not found")
        
        document.ocr_text = ocr_text
        document.page_count = page_count
        if ocr_metadata is not None:
            document.ocr_metadata = ocr_metadata
        await self.session.flush()
        return document

