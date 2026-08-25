"""RAG 검색 도구.

내부 콘텐츠(요약, 전사 텍스트)를 검색합니다.
Phase 2: 하이브리드 검색 (키워드 + 벡터 유사도) 지원
"""
from __future__ import annotations

from loguru import logger
from typing import Any
from uuid import UUID

from sqlalchemy import select, or_, func, literal
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import File, Content, FileStatus, Transcription, Document
from ...db.session import AsyncSessionLocal




class RAGSearchError(RuntimeError):
    """RAG 검색 실패 예외."""


async def search_internal_content(
    query: str,
    *,
    settings: Any,
    user_id: UUID | str | None,
    limit: int = 5,
    content_ids: list[UUID] | None = None,
    search_mode: str = "hybrid",  # "keyword", "vector", "hybrid"
    keyword_weight: float = 0.6,
    vector_weight: float = 0.4,
) -> list[dict]:
    """내부 콘텐츠에서 검색 (하이브리드).

    Phase 2: 키워드 검색과 벡터 유사도 검색을 결합한 하이브리드 검색 지원

    Args:
        query: 검색 쿼리
        settings: 애플리케이션 설정
        limit: 최대 결과 수
        content_ids: 특정 콘텐츠 ID로 제한 (선택)
        search_mode: "keyword" (키워드만), "vector" (벡터만), "hybrid" (하이브리드)
        keyword_weight: 하이브리드 모드에서 키워드 검색 가중치
        vector_weight: 하이브리드 모드에서 벡터 검색 가중치

    Returns:
        검색 결과 목록

    Raises:
        RAGSearchError: 검색 실패
    """
    query = query.strip()
    if not query:
        raise RAGSearchError("검색어가 비어있습니다")
    if not user_id:
        raise RAGSearchError("user_id is required for internal content search")
    owner_id = UUID(str(user_id))

    logger.info(f"[RAG] Hybrid search: query={query[:50]}, mode={search_mode}")

    try:
        async with AsyncSessionLocal() as session:
            from ...repositories.content_repository import ContentRepository

            repo = ContentRepository(session)

            # 1. 키워드 검색
            keyword_results = await _keyword_search(
                session,
                query,
                user_id=owner_id,
                limit=limit * 2,
                content_ids=content_ids,
            )

            # 2. 벡터 검색 (hybrid/vector 모드만)
            vector_results = []
            if search_mode in ["vector", "hybrid"]:
                try:
                    from ...utils.embedding import create_embedding

                    query_embedding = await create_embedding(query)

                    if query_embedding:
                        vector_results = await repo.vector_search_contents(
                            query_embedding=query_embedding,
                            limit=limit * 2,
                            content_ids=content_ids,
                            user_id=owner_id,
                        )
                    else:
                        logger.warning(
                            "[RAG] Failed to generate query embedding, falling back to keyword"
                        )
                        search_mode = "keyword"
                except Exception as e:
                    logger.warning(f"[RAG] Vector search failed: {e}, falling back to keyword")
                    search_mode = "keyword"

            # 3. 결과 병합
            if search_mode == "hybrid" and vector_results:
                final_results = _merge_hybrid(
                    keyword_results, vector_results, keyword_weight, vector_weight, limit
                )
            elif search_mode == "vector" and vector_results:
                final_results = [(content.file, score) for content, score in vector_results[:limit]]
            else:
                final_results = keyword_results[:limit]

            # 4. 포맷팅
            search_results = []
            keywords = [kw.strip() for kw in query.split() if len(kw.strip()) >= 2]
            if not keywords:
                keywords = [query]

            for i, (file, score) in enumerate(final_results):
                content = file.content
                snippet = _extract_relevant_snippet(
                    content.summary_md or "" if content else "", keywords
                )

                search_results.append(
                    {
                        "position": i + 1,
                        "title": (content.title if content else None) or file.filename,
                        "url": f"/contents/{file.id}",
                        "snippet": snippet,
                        "source": "rag",
                        "content_id": file.id,
                        "content_type": (
                            str(file.content_type.value) if file.content_type else "unknown"
                        ),
                        "score": round(score, 4),
                        "search_mode": search_mode,
                    }
                )

            logger.info(f"[RAG] Found {len(search_results)} results (mode={search_mode})")
            return search_results

    except Exception as e:
        logger.error(f"[RAG] Search failed: {e}")
        raise RAGSearchError(f"내부 검색 실패: {e}")


async def _keyword_search(
    session: AsyncSession,
    query: str,
    user_id: UUID | str,
    limit: int,
    content_ids: list[UUID] | None,
) -> list[tuple[File, float]]:
    """키워드 검색 (기존 ILIKE 로직).

    Args:
        session: DB 세션
        query: 검색 쿼리
        limit: 최대 결과 수
        content_ids: 특정 콘텐츠 ID로 제한

    Returns:
        (File, score) 튜플 리스트 (score는 키워드 검색이므로 1.0)
    """
    keywords = [kw.strip() for kw in query.split() if len(kw.strip()) >= 2]
    if not keywords:
        keywords = [query]

    stmt = (
        select(File, literal(1.0).label("score"))  # 키워드는 점수 1.0
        .join(Content, Content.file_id == File.id)
        .options(selectinload(File.content))
        .where(
            Content.status == FileStatus.COMPLETED,
            Content.summary_md.isnot(None),
            Content.user_id == user_id,
        )
    )

    if content_ids:
        stmt = stmt.where(File.id.in_(content_ids))

    # 키워드 조건
    keyword_conditions = []
    for kw in keywords[:5]:
        keyword_conditions.append(
            or_(
                Content.title.ilike(f"%{kw}%"),
                Content.summary_md.ilike(f"%{kw}%"),
            )
        )

    if keyword_conditions:
        stmt = stmt.where(or_(*keyword_conditions))

    stmt = stmt.order_by(File.created_at.desc()).limit(limit)

    result = await session.execute(stmt)
    rows = result.all()

    return [(row.File, row.score) for row in rows]


def _merge_hybrid(
    keyword_results: list[tuple[File, float]],
    vector_results: list[tuple[Content, float]],
    keyword_weight: float,
    vector_weight: float,
    limit: int,
) -> list[tuple[File, float]]:
    """RRF (Reciprocal Rank Fusion) 기반 하이브리드 병합.

    Args:
        keyword_results: (File, score) 키워드 검색 결과
        vector_results: (Content, similarity) 벡터 검색 결과
        keyword_weight: 키워드 가중치
        vector_weight: 벡터 가중치
        limit: 최대 결과 수

    Returns:
        (File, combined_score) 병합 결과
    """
    K = 60  # RRF 상수

    # 파일 ID별 점수 집계
    scores: dict[UUID, float] = {}
    file_map: dict[UUID, File] = {}

    # 키워드 결과 처리
    for rank, (file, _) in enumerate(keyword_results, start=1):
        file_id = file.id
        rrf_score = keyword_weight / (K + rank)
        scores[file_id] = scores.get(file_id, 0) + rrf_score
        file_map[file_id] = file

    # 벡터 결과 처리
    for rank, (content, similarity) in enumerate(vector_results, start=1):
        file_id = content.file_id
        # 벡터는 코사인 유사도(0~1)와 RRF 조합
        rrf_score = vector_weight * (similarity + 1 / (K + rank))
        scores[file_id] = scores.get(file_id, 0) + rrf_score

        if file_id not in file_map and content.file:
            file_map[file_id] = content.file

    # 점수 기준 정렬
    sorted_results = sorted(
        [(file_map[fid], score) for fid, score in scores.items()],
        key=lambda x: x[1],
        reverse=True,
    )

    return sorted_results[:limit]


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
    content_ids: list[UUID],
    *,
    user_id: UUID | str | None,
    include_transcription: bool = False,
) -> list[dict]:
    """특정 콘텐츠의 상세 컨텍스트 조회.

    Args:
        content_ids: 콘텐츠 ID 목록
        include_transcription: 전사 텍스트 포함 여부

    Returns:
        콘텐츠 컨텍스트 목록
    """
    if not content_ids or not user_id:
        return []
    owner_id = UUID(str(user_id))

    try:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(File)
                .join(Content, Content.file_id == File.id)
                .options(selectinload(File.content).selectinload(Content.transcription_result))
                .where(File.id.in_(content_ids), Content.user_id == owner_id)
            )
            result = await session.execute(stmt)
            files = result.scalars().all()

            contexts = []
            for file in files:
                content = file.content
                context = {
                    "id": file.id,
                    "title": (content.title if content else None) or file.filename,
                    "summary": content.summary_md or "" if content else "",
                    "content_type": str(file.content_type.value) if file.content_type else "unknown",
                }

                # 전사 텍스트 포함 (선택적)
                if include_transcription and content and content.transcription_result:
                    trans_data = content.transcription_result.transcription
                    if isinstance(trans_data, dict):
                        context["transcription"] = trans_data.get("text", "")[:2000]

                contexts.append(context)

            return contexts

    except Exception as e:
        logger.error(f"[RAG] Failed to get content context: {e}")
        return []
