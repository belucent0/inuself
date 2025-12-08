"""Document 스키마."""

from pydantic import BaseModel, Field
from typing import Any


class DocumentBaseSchema(BaseModel):
    """Document 기본 스키마."""

    id: int
    file_id: int
    ocr_text: str
    page_count: int = 0
    ocr_metadata: dict[str, Any] = Field(default_factory=dict)
    html_content: str | None = None  # Docling HTML 출력 (뷰어용)

    class Config:
        from_attributes = True


class DocumentDetail(DocumentBaseSchema):
    """Document 상세 정보."""

    pass


