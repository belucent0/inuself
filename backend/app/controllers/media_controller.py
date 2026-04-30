"""미디어 파일 프록시 API.

보안이 중요한 미디어 파일을 위한 인증 기반 프록시.
- JWT 인증 확인 후에만 접근 가능
- Range 요청 지원 (영상 탐색, 부분 다운로드)
- 로컬 캐시 활용
"""
from __future__ import annotations

import mimetypes
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..repositories.content_repository import ContentRepository
from ..services.media_cache_service import get_media_cache_service, MediaCacheService


router = APIRouter(prefix="/api/media", tags=["media"])


# TODO: 실제 인증 구현 시 교체
async def get_current_user_id() -> UUID:
    """현재 사용자 ID 반환 (임시 구현)."""
    return UUID("01234567-89ab-cdef-0123-456789abcdef")


def parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int]:
    """Range 헤더 파싱.

    Args:
        range_header: "bytes=start-end" 형식
        file_size: 전체 파일 크기

    Returns:
        (start, end) 튜플
    """
    if not range_header:
        return 0, file_size - 1

    try:
        # "bytes=start-end" 파싱
        range_spec = range_header.replace("bytes=", "")
        if "-" not in range_spec:
            return 0, file_size - 1

        parts = range_spec.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1

        # 범위 검증
        start = max(0, start)
        end = min(end, file_size - 1)

        if start > end:
            start = 0
            end = file_size - 1

        return start, end

    except (ValueError, IndexError):
        return 0, file_size - 1


@router.get("/{content_id}")
async def stream_media(
    content_id: UUID,
    request: Request,
    range: str | None = Header(default=None, alias="Range"),
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    cache_service: MediaCacheService = Depends(get_media_cache_service),
):
    """미디어 파일 스트리밍.

    - 인증된 사용자만 접근 가능
    - Range 요청 지원 (영상 탐색)
    - 로컬 캐시 활용
    """
    logger.info(f"[Media] Request: content_id={content_id}, user={user_id}, range={range}")

    # 1. 콘텐츠 조회 및 권한 확인
    repo = ContentRepository(session)
    content = await repo.get_by_file_id(content_id)

    if not content:
        raise HTTPException(status_code=404, detail="콘텐츠를 찾을 수 없습니다")

    # TODO: 권한 확인 로직 (소유자 또는 공유 대상)
    # 현재는 인증된 사용자면 모두 허용
    # if content.owner_id != user_id and not is_shared_to(content, user_id):
    #     raise HTTPException(status_code=403, detail="접근 권한이 없습니다")

    # 2. object_key 조회
    object_key = content.file.object_key if content.file else None
    if not object_key:
        raise HTTPException(status_code=404, detail="미디어 파일이 없습니다")

    # 3. 파일 정보 조회
    file_info = await cache_service.get_file_info(object_key)
    if not file_info:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")

    file_size = file_info["size"]

    # 4. MIME 타입 추론
    content_type = mimetypes.guess_type(object_key)[0] or "application/octet-stream"

    # 5. Range 요청 처리
    start, end = parse_range_header(range, file_size)
    content_length = end - start + 1

    # 6. 스트리밍 응답
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(content_length),
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'inline; filename="{object_key.split("/")[-1]}"',
        # 캐시 제어: 브라우저 캐시 허용하되 재검증 필요
        "Cache-Control": "private, max-age=3600",
    }

    if range:
        # 206 Partial Content
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        status_code = 206
    else:
        status_code = 200

    logger.debug(
        f"[Media] Streaming: {object_key}, "
        f"range={start}-{end}/{file_size}, "
        f"content_type={content_type}"
    )

    return StreamingResponse(
        cache_service.stream_file(object_key, start, end),
        status_code=status_code,
        headers=headers,
        media_type=content_type,
    )


@router.get("/cover/{content_id}")
async def get_cover_image(
    content_id: UUID,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    cache_service: MediaCacheService = Depends(get_media_cache_service),
):
    """커버 이미지 프록시.

    콘텐츠의 AI 생성 커버 이미지를 S3에서 스트리밍합니다.
    """
    repo = ContentRepository(session)
    content = await repo.get_by_file_id(content_id)

    if not content:
        raise HTTPException(status_code=404, detail="콘텐츠를 찾을 수 없습니다")

    if not content.cover_image_key:
        raise HTTPException(status_code=404, detail="커버 이미지가 없습니다")

    file_info = await cache_service.get_file_info(content.cover_image_key)
    if not file_info:
        raise HTTPException(status_code=404, detail="커버 이미지 파일을 찾을 수 없습니다")

    file_size = file_info["size"]

    return StreamingResponse(
        cache_service.stream_file(content.cover_image_key, 0, file_size - 1),
        status_code=200,
        headers={
            "Content-Type": "image/png",
            "Content-Length": str(file_size),
            "Cache-Control": "public, max-age=86400",
        },
        media_type="image/png",
    )


@router.head("/{content_id}")
async def head_media(
    content_id: UUID,
    session: AsyncSession = Depends(get_session),
    user_id: UUID = Depends(get_current_user_id),
    cache_service: MediaCacheService = Depends(get_media_cache_service),
):
    """미디어 파일 메타데이터 조회 (HEAD 요청).

    브라우저가 Range 요청 전에 파일 크기 확인용으로 사용.
    """
    # 1. 콘텐츠 조회
    repo = ContentRepository(session)
    content = await repo.get_by_file_id(content_id)

    if not content:
        raise HTTPException(status_code=404, detail="콘텐츠를 찾을 수 없습니다")

    object_key = content.file.object_key if content.file else None
    if not object_key:
        raise HTTPException(status_code=404, detail="미디어 파일이 없습니다")

    # 2. 파일 정보 조회
    file_info = await cache_service.get_file_info(object_key)
    if not file_info:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")

    file_size = file_info["size"]
    content_type = mimetypes.guess_type(object_key)[0] or "application/octet-stream"

    headers = {
        "Content-Type": content_type,
        "Content-Length": str(file_size),
        "Accept-Ranges": "bytes",
    }

    return StreamingResponse(
        iter([]),  # 빈 바디
        status_code=200,
        headers=headers,
        media_type=content_type,
    )
