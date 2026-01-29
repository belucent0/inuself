"""RAG 검색 도구.

내부 콘텐츠(요약, 전사 텍스트)를 검색합니다.
현재는 간단한 키워드 매칭을 사용하며, 나중에 pgvector로 업그레이드 가능합니다.
"""
from __future__ import annotations

from loguru import logger
from typing import Any

from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import File, FileStatus, Transcription, Document
from ...db.session import AsyncSessionLocal




class RAGSearchError(RuntimeError):
    """RAG 검색 실패 예외."""


async def search_internal_content(
    query: str,
    *,
    settings: Any,
    limit: int = 5,
    content_ids: list[int] | None = None,
) -> list[dict]:
    """내부 콘텐츠에서 검색.

    현재는 PostgreSQL의 ILIKE를 사용한 간단한 키워드 검색입니다.
    추후 pgvector를 사용한 시맨틱 검색으로 업그레이드 가능합니다.

    Args:
        query: 검색 쿼리
        settings: 애플리케이션 설정
        limit: 최대 결과 수
        content_ids: 특정 콘텐츠 ID로 제한 (선택)

    Returns:
        검색 결과 목록

    Raises:
        RAGSearchError: 검색 실패
    """
    query = query.strip()
    if not query:
        raise RAGSearchError("검색어가 비어있습니다")

    # 검색 키워드 추출 (간단한 토큰화)
    keywords = [kw.strip() for kw in query.split() if len(kw.strip()) >= 2]
    if not keywords:
        keywords = [query]

    logger.info(f"[RAG] Searching internal content: query={query[:50]}, keywords={keywords}")

    try:
        async with AsyncSessionLocal() as session:
            # 완료된 콘텐츠만 검색
            stmt = select(File).where(
                File.status == FileStatus.COMPLETED,
                File.summary_md.isnot(None),
            )

            # 특정 콘텐츠 ID로 제한
            if content_ids:
                stmt = stmt.where(File.id.in_(content_ids))

            # 키워드 검색 조건 (title 또는 summary_md에서 검색)
            keyword_conditions = []
            for kw in keywords[:5]:  # 최대 5개 키워드
                keyword_conditions.append(
                    or_(
                        File.title.ilike(f"%{kw}%"),
                        File.summary_md.ilike(f"%{kw}%"),
                    )
                )

            if keyword_conditions:
                stmt = stmt.where(or_(*keyword_conditions))

            # 최신순 정렬, 제한
            stmt = stmt.order_by(File.created_at.desc()).limit(limit)

            result = await session.execute(stmt)
            files = result.scalars().all()

            # 결과 포맷팅
            search_results = []
            for i, file in enumerate(files):
                # 요약에서 관련 스니펫 추출
                snippet = _extract_relevant_snippet(file.summary_md or "", keywords)

                search_results.append({
                    "position": i + 1,
                    "title": file.title or file.filename,
                    "url": f"/contents/{file.id}",  # 내부 URL
                    "snippet": snippet,
                    "source": "rag",
                    "content_id": file.id,
                    "content_type": str(file.content_type.value) if file.content_type else "unknown",
                })

            logger.info(f"[RAG] Found {len(search_results)} internal results")
            return search_results

    except Exception as e:
        logger.error(f"[RAG] Search failed: {e}")
        raise RAGSearchError(f"내부 검색 실패: {e}")


def _extract_relevant_snippet(text: str, keywords: list[str], max_length: int = 200) -> str:
    """텍스트에서 키워드와 관련된 스니펫 추출.

    Args:
        text: 전체 텍스트
        keywords: 검색 키워드
        max_length: 최대 스니펫 길이

    Returns:
        관련 스니펫
    """
    if not text:
        return ""

    text_lower = text.lower()

    # 키워드가 포함된 위치 찾기
    for kw in keywords:
        kw_lower = kw.lower()
        pos = text_lower.find(kw_lower)
        if pos >= 0:
            # 키워드 주변 텍스트 추출
            start = max(0, pos - 50)
            end = min(len(text), pos + max_length - 50)

            snippet = text[start:end].strip()

            # 문장 경계 정리
            if start > 0:
                snippet = "..." + snippet
            if end < len(text):
                snippet = snippet + "..."

            return snippet

    # 키워드를 찾지 못하면 앞부분 반환
    return text[:max_length].strip() + ("..." if len(text) > max_length else "")


async def get_content_context(
    content_ids: list[int],
    *,
    include_transcription: bool = False,
) -> list[dict]:
    """특정 콘텐츠의 상세 컨텍스트 조회.

    Args:
        content_ids: 콘텐츠 ID 목록
        include_transcription: 전사 텍스트 포함 여부

    Returns:
        콘텐츠 컨텍스트 목록
    """
    if not content_ids:
        return []

    try:
        async with AsyncSessionLocal() as session:
            stmt = select(File).where(File.id.in_(content_ids))
            result = await session.execute(stmt)
            files = result.scalars().all()

            contexts = []
            for file in files:
                context = {
                    "id": file.id,
                    "title": file.title or file.filename,
                    "summary": file.summary_md or "",
                    "content_type": str(file.content_type.value) if file.content_type else "unknown",
                }

                # 전사 텍스트 포함 (선택적)
                if include_transcription and file.transcription:
                    trans_data = file.transcription.transcription
                    if isinstance(trans_data, dict):
                        context["transcription"] = trans_data.get("text", "")[:2000]

                contexts.append(context)

            return contexts

    except Exception as e:
        logger.error(f"[RAG] Failed to get content context: {e}")
        return []
