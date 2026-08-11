"""콘텐츠 컨텍스트 로더.

콘텐츠 상세 채팅 시 요약/전사 텍스트를 AI 프롬프트에 주입하기 위한 유틸리티.
"""
from __future__ import annotations

from loguru import logger
from typing import Any
from uuid import UUID

from sqlalchemy.orm import selectinload
from sqlalchemy import select

from ...db.models import File, Content, Transcription
from ...db.session import AsyncSessionLocal


# 토큰 예산 상수
_SUMMARY_CHAR_LIMIT = 4000
_TRANSCRIPTION_CHAR_LIMIT = 8000
_TOTAL_CHAR_LIMIT = 12000


async def load_content_context(
    content_ids: list,
    source_options: dict[str, Any] | None = None,
    *,
    user_id: UUID | str | None,
) -> str:
    """콘텐츠 ID 목록으로부터 AI 주입용 컨텍스트 문자열을 생성합니다.

    Args:
        content_ids: 콘텐츠(파일) ID 목록
        source_options: {
            "include_summary": bool (기본 True),
            "include_transcription": bool (기본 True),
            "speaker_filter": list[str] | None (None이면 전체),
        }

    Returns:
        포맷된 컨텍스트 문자열 (없으면 빈 문자열)
    """
    if not content_ids or not user_id:
        return ""
    owner_id = UUID(str(user_id))

    opts = source_options or {}
    include_summary = opts.get("include_summary", True)
    include_transcription = opts.get("include_transcription", True)
    speaker_filter: list[str] | None = opts.get("speaker_filter", None)

    try:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(File)
                .options(
                    selectinload(File.content).selectinload(Content.transcription_result)
                )
                .join(Content, Content.file_id == File.id)
                .where(File.id.in_(content_ids), Content.user_id == owner_id)
            )
            result = await session.execute(stmt)
            files = result.scalars().all()

            if not files:
                return ""

            parts: list[str] = []
            total_chars = 0

            for file in files:
                content = file.content
                if not content:
                    continue

                title = content.title or file.filename
                section_parts: list[str] = [f"### 콘텐츠: {title}"]

                # 요약 추가
                if include_summary and content.summary_md:
                    summary_text = content.summary_md[:_SUMMARY_CHAR_LIMIT]
                    if len(content.summary_md) > _SUMMARY_CHAR_LIMIT:
                        summary_text += "...(이하 생략)"
                    section_parts.append(f"**요약:**\n{summary_text}")

                # 전사 추가
                if include_transcription and content.transcription_result:
                    trans_data = content.transcription_result.transcription
                    if isinstance(trans_data, dict):
                        segments = trans_data.get("segments", [])
                        if segments:
                            trans_lines: list[str] = []
                            char_count = 0
                            for seg in segments:
                                speaker = seg.get("speaker", "")
                                text = seg.get("text", "").strip()
                                if not text:
                                    continue
                                # 화자 필터 적용
                                if speaker_filter is not None and speaker not in speaker_filter:
                                    continue
                                line = f"[{speaker}] {text}" if speaker else text
                                if char_count + len(line) > _TRANSCRIPTION_CHAR_LIMIT:
                                    trans_lines.append("...(이하 생략)")
                                    break
                                trans_lines.append(line)
                                char_count += len(line)

                            if trans_lines:
                                section_parts.append(
                                    "**전사:**\n" + "\n".join(trans_lines)
                                )

                section_text = "\n\n".join(section_parts)
                if total_chars + len(section_text) > _TOTAL_CHAR_LIMIT:
                    remaining = _TOTAL_CHAR_LIMIT - total_chars
                    if remaining > 200:
                        parts.append(section_text[:remaining] + "...(생략)")
                    break
                parts.append(section_text)
                total_chars += len(section_text)

            if not parts:
                return ""

            return "\n\n---\n\n".join(parts)

    except Exception as e:
        logger.error(f"[ContentContext] Failed to load content context: {e}")
        return ""
