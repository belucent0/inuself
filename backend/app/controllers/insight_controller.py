"""영상 인사이트 글 API."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.auth import get_current_user_id
from ..core.logging import logger
from ..db.session import AsyncSessionLocal
from ..db.session import get_session
from ..schemas.insight import (
    InsightPostCreateRequest,
    InsightPostDetail,
    InsightPostListResponse,
    InsightPostUpdateRequest,
    InsightResearchRequest,
)
from ..services.insight_post_service import InsightPostService


router = APIRouter(prefix="/insight-posts", tags=["insight-posts"])


async def run_insight_generation_background(
    post_id: UUID,
    user_id: UUID,
    payload: dict,
) -> None:
    async with AsyncSessionLocal() as session:
        service = InsightPostService(session)
        request = InsightPostCreateRequest(**payload)
        try:
            await service.generate_pending_post(post_id, user_id=user_id, request=request)
        except Exception as exc:
            logger.warning("[InsightPost] Background generation failed: {}", exc)


async def get_service(session: AsyncSession = Depends(get_session)) -> InsightPostService:
    return InsightPostService(session)


@router.get("", response_model=InsightPostListResponse)
async def list_insight_posts(
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=100),
    user_id: UUID = Depends(get_current_user_id),
    service: InsightPostService = Depends(get_service),
):
    return await service.list_posts(user_id=user_id, page=page, page_size=page_size)


@router.post("/from-content/{content_id}", response_model=InsightPostDetail)
async def create_insight_post_from_content(
    content_id: UUID,
    request: InsightPostCreateRequest,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user_id),
    service: InsightPostService = Depends(get_service),
):
    try:
        post = await service.create_generation_job_from_content(
            content_id,
            user_id=user_id,
            request=request,
        )
        background_tasks.add_task(
            run_insight_generation_background,
            post.id,
            user_id,
            request.model_dump(),
        )
        return post
    except ValueError as exc:
        status_code = 503 if "AI Gateway" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/{post_id}", response_model=InsightPostDetail)
async def get_insight_post(
    post_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
    service: InsightPostService = Depends(get_service),
):
    try:
        return await service.get_post(post_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{post_id}/regenerate", response_model=InsightPostDetail)
async def regenerate_insight_post(
    post_id: UUID,
    request: InsightPostCreateRequest,
    background_tasks: BackgroundTasks,
    user_id: UUID = Depends(get_current_user_id),
    service: InsightPostService = Depends(get_service),
):
    try:
        post = await service.restart_generation(
            post_id,
            user_id=user_id,
            request=request,
        )
        background_tasks.add_task(
            run_insight_generation_background,
            post.id,
            user_id,
            request.model_dump(),
        )
        return post
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{post_id}", response_model=InsightPostDetail)
async def update_insight_post(
    post_id: UUID,
    request: InsightPostUpdateRequest,
    user_id: UUID = Depends(get_current_user_id),
    service: InsightPostService = Depends(get_service),
):
    try:
        return await service.update_post(post_id, user_id=user_id, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{post_id}/research", response_model=InsightPostDetail)
async def research_insight_post(
    post_id: UUID,
    request: InsightResearchRequest,
    user_id: UUID = Depends(get_current_user_id),
    service: InsightPostService = Depends(get_service),
):
    try:
        return await service.run_research(post_id, user_id=user_id, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
