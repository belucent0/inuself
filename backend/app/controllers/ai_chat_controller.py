"""AI 채팅 API 엔드포인트 (LangGraph 기반).

V8.0: LangGraph 워크플로우를 사용한 AI 모드 채팅 API.
V8.5: conversation_id → thread_id 용어 통일
V8.6: RESTful API 구조 개선 - /api/threads로 통일
V9.0: Phase 1 - user_id 기반 스레드 관리, DB 영속화
V9.1: Agent Worker 응답 생성 - 클라이언트 연결 끊김에도 계속 생성

확인 후 라우팅 + SSE 재연결 + 메시지 상태 세분화:
- POST /api/threads - 스레드+메시지 생성 후 Agent Worker 큐 적재
- POST /api/threads/{thread_id}/messages - 메시지 추가 후 Agent Worker 큐 적재
- GET /api/threads/{thread_id}/messages/{message_id}/stream - SSE 스트리밍
- 메시지 상태: queued → analyzing → searching → thinking → generating → completed
- 5초마다 partial_content DB 스냅샷 저장

기존 chat_controller.py와 별도로 동작합니다.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.auth import get_current_user_id
from ..core.redis import get_redis_client
from ..db.models import Content
from ..db.session import get_session, async_session_factory
from ..services.thread_service import (
    get_thread_service,
    ThreadService,
)
from ..services.agent_dispatcher import (
    AGENT_JOB_METADATA_KEY,
    dispatch_agent_job,
)
from ..utils.task_queue_adapter import (
    ACTIVE_JOB_TTL,
    AGENT_EVENT_CHANNEL_PREFIX,
    AGENT_THREAD_KEY_PREFIX,
)

router = APIRouter(prefix="/api/threads", tags=["threads"])


class RegenerateRequest(BaseModel):
    """답변 재생성 요청."""

    model_config = ConfigDict(extra="forbid")

    mode: str = Field(
        default="auto",
        description="AI 모드 (auto, simple, search, rag, reasoning, hybrid)",
    )
    context: dict | None = Field(default=None, description="추가 컨텍스트")
    reasoning: Literal["auto", "none", "low", "medium", "high"] = "auto"
    allow_remote: bool = False


class ThreadListResponse(BaseModel):
    """스레드 목록 응답."""

    threads: list[dict] = Field(..., description="스레드 목록")
    total: int = Field(..., description="전체 개수")


class ThreadDetailResponse(BaseModel):
    """스레드 상세 응답."""

    thread_id: str
    title: str
    messages: list[dict]
    created_at: float
    updated_at: float
    content_id: str | None = None


# === 의존성 ===


async def get_svc(
    session: AsyncSession = Depends(get_session, scope="function"),
) -> ThreadService:
    """스레드 서비스 의존성 (DB 세션 포함)."""
    return get_thread_service(session)


async def _clear_agent_thread_slot(thread_id: str, message_id: str) -> None:
    redis = get_redis_client()
    with suppress(RedisError):
        await redis.eval(
            """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            f"{AGENT_THREAD_KEY_PREFIX}{thread_id}",
            message_id,
        )


async def _commit_and_enqueue_agent(
    *,
    session: AsyncSession,
    svc: ThreadService,
    thread_id: str,
    user_id: UUID,
    user_message_id: str,
    assistant_message_id: str,
) -> None:
    """Commit the turn, then hand it to the Agent Worker exactly once per thread."""
    redis = get_redis_client()
    reserved = await redis.set(
        f"{AGENT_THREAD_KEY_PREFIX}{thread_id}",
        assistant_message_id,
        nx=True,
        ex=ACTIVE_JOB_TTL,
    )
    if not reserved:
        raise HTTPException(
            status_code=409,
            detail="This thread already has an active response",
        )

    job = {
        "thread_id": thread_id,
        "user_id": str(user_id),
        "user_message_id": user_message_id,
    }
    try:
        outbox_message = await svc.update_message_metadata(
            assistant_message_id,
            **{AGENT_JOB_METADATA_KEY: job},
        )
        if outbox_message is None:
            raise RuntimeError("Assistant message disappeared before dispatch")
        await session.commit()
    except Exception as exc:
        await _clear_agent_thread_slot(thread_id, assistant_message_id)
        await session.rollback()
        logger.exception(
            "[Thread] Agent turn commit failed: thread_id={} message_id={}",
            thread_id,
            assistant_message_id,
        )
        raise HTTPException(
            status_code=503,
            detail="Agent turn could not be saved",
        ) from exc

    try:
        await dispatch_agent_job(assistant_message_id, job)
    except Exception as exc:
        # Broker errors are ambiguous: keep the DB outbox queued for safe republish.
        logger.warning(
            "[Thread] Agent publish deferred: thread_id={} message_id={} error={}",
            thread_id,
            assistant_message_id,
            exc,
        )


def _sse_event(event_type: str, data: Any) -> str:
    payload = json.dumps(
        {"type": event_type, "data": data},
        ensure_ascii=False,
        default=str,
    )
    return f"data: {payload}\n\n"


async def _relay_agent_events(message_id: str):
    """Relay live Pub/Sub events with PostgreSQL snapshot/final recovery."""

    async def db_get_message():
        async with async_session_factory() as session:
            return await get_thread_service(session).get_message(message_id)

    redis = get_redis_client()
    pubsub = redis.pubsub()
    last_partial = ""
    persisted_content_sequence = 0
    try:
        await pubsub.subscribe(f"{AGENT_EVENT_CHANNEL_PREFIX}{message_id}")
        current = await db_get_message()
        if not current:
            yield _sse_event("error", "Assistant message not found")
            return
        if current.status == "completed":
            yield _sse_event("content", current.content)
            yield _sse_event(
                "done",
                {"content": current.content, "metadata": current.metadata},
            )
            return
        if current.status in {"failed", "cancelled"}:
            yield _sse_event("error", current.content or current.status)
            return
        if current.partial_content:
            last_partial = current.partial_content
            persisted_content_sequence = int(
                current.metadata.get("_content_sequence", 0)
            )
            yield _sse_event("partial_restore", last_partial)
        yield _sse_event("status", current.status)

        while True:
            event = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=5.0,
            )
            if event:
                raw = event["data"]
                payload = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                with suppress(json.JSONDecodeError):
                    parsed = json.loads(payload)
                    sequence = parsed.get("content_sequence")
                    if parsed.get("type") == "token" and isinstance(sequence, int):
                        if sequence <= persisted_content_sequence:
                            continue
                    elif parsed.get("type") == "content" and isinstance(sequence, int):
                        persisted_content_sequence = max(
                            persisted_content_sequence,
                            sequence,
                        )
                    if parsed.get("type") in {"done", "error"}:
                        yield f"data: {payload}\n\n"
                        return
                yield f"data: {payload}\n\n"
                continue

            current = await db_get_message()
            if not current:
                yield _sse_event("error", "Assistant message not found")
                return
            if current.status == "completed":
                yield _sse_event("content", current.content)
                yield _sse_event(
                    "done",
                    {"content": current.content, "metadata": current.metadata},
                )
                return
            if current.status in {"failed", "cancelled"}:
                yield _sse_event("error", current.content or current.status)
                return
            if current.partial_content and current.partial_content != last_partial:
                last_partial = current.partial_content
                persisted_content_sequence = max(
                    persisted_content_sequence,
                    int(current.metadata.get("_content_sequence", 0)),
                )
                yield _sse_event("partial_restore", last_partial)
            yield ": ping\n\n"
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("[Thread] SSE relay failed: message_id={}", message_id)
        yield _sse_event("error", "Stream temporarily unavailable. Please reconnect.")
    finally:
        with suppress(Exception):
            await pubsub.unsubscribe(f"{AGENT_EVENT_CHANNEL_PREFIX}{message_id}")
        with suppress(Exception):
            await pubsub.aclose()


def _normalize_mode_name(mode: Any) -> str:
    """AIMode/문자열 모드를 UI 친화 문자열로 정규화."""
    if mode is None:
        return "simple"

    # Enum-like object
    value = getattr(mode, "value", None)
    if isinstance(value, str) and value:
        return value.lower()

    text = str(mode).strip()
    if not text:
        return "simple"

    # 'AIMode.SEARCH' -> 'search'
    if text.startswith("AIMode."):
        text = text.split(".", 1)[1]

    return text.lower()


def _build_agent_metadata(
    *,
    context: dict | None,
    reasoning: Literal["auto", "none", "low", "medium", "high"],
    allow_remote: bool,
) -> dict[str, Any]:
    metadata: dict[str, Any] = dict(context) if isinstance(context, dict) else {}
    metadata.pop("model", None)
    metadata.pop("llm_model", None)
    metadata.pop(AGENT_JOB_METADATA_KEY, None)
    metadata.pop("_content_sequence", None)
    metadata["reasoning"] = reasoning
    metadata["allow_remote"] = allow_remote

    # content_id → content_ids 정규화 (RAG 필터링 일관성)
    if "content_id" in metadata and "content_ids" not in metadata:
        metadata["content_ids"] = [metadata["content_id"]]

    return metadata


async def _validate_content_ownership(
    session: AsyncSession,
    user_id: UUID,
    metadata: dict[str, Any],
) -> None:
    """Reject missing or foreign File IDs before persisting an Agent run."""
    raw_many = metadata.get("content_ids")
    if raw_many is not None and not isinstance(raw_many, list):
        raise HTTPException(status_code=400, detail="content_ids must be a list of UUIDs")

    requested = list(raw_many or [])
    if metadata.get("content_id") is not None:
        requested.append(metadata["content_id"])
    if not requested:
        return

    try:
        file_ids = list(dict.fromkeys(UUID(str(value)) for value in requested))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="content IDs must be valid UUIDs") from exc

    result = await session.execute(
        select(Content.file_id).where(
            Content.file_id.in_(file_ids),
            Content.user_id == user_id,
        )
    )
    owned_ids = set(result.scalars().all())
    if owned_ids != set(file_ids):
        raise HTTPException(status_code=404, detail="Content not found")

    metadata["content_ids"] = [str(file_id) for file_id in file_ids]
    if metadata.get("content_id") is not None:
        metadata["content_id"] = str(UUID(str(metadata["content_id"])))


def _build_langfuse_trace_metadata(
    *,
    base_context: dict | None,
    thread_id: str,
    user_id: UUID,
    mode: str | None,
    route_name: str,
    turn_index: int,
    user_message_id: str | None = None,
    assistant_message_id: str | None = None,
) -> dict:
    """Langfuse trace metadata를 구성합니다.

    기존 context를 보존하면서, 대화/턴 연결에 필요한 식별자를 추가합니다.
    """

    metadata = dict(base_context) if isinstance(base_context, dict) else {}
    metadata.update(
        {
            "thread_id": thread_id,
            "user_id": str(user_id),
            "mode_requested": _normalize_mode_name(mode),
            "route_name": route_name,
            "turn_index": max(1, int(turn_index)),
        }
    )

    if user_message_id:
        metadata["user_message_id"] = user_message_id
    if assistant_message_id:
        metadata["assistant_message_id"] = assistant_message_id

    return metadata


class UpdateThreadMetadataRequest(BaseModel):
    """스레드 메타데이터 업데이트 요청."""

    metadata: dict = Field(..., description="병합할 메타데이터 키-값 쌍")


@router.get("", response_model=ThreadListResponse)
async def list_threads(
    limit: int = 20,
    offset: int = 0,
    content_id: str | None = None,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session, scope="function"),
):
    """스레드 목록 조회.

    GET /api/threads
    GET /api/threads?content_id=X  — 특정 콘텐츠의 스레드만 조회 (메시지 포함)
    content_id는 프론트엔드의 file.id이며, 내부적으로 content.id로 변환 후 조회한다.
    """
    if content_id:
        # file_id → content.id 변환 (AiThread.content_id는 content.id FK)
        from ..repositories.content_repository import ContentRepository
        _content_repo = ContentRepository(session)
        try:
            _content = await _content_repo.get_by_file_id(UUID(content_id))
            actual_content_id = str(_content.id) if _content else content_id
        except Exception:
            actual_content_id = content_id
        threads = await svc.get_threads_by_content(
            user_id=user_id, content_id=actual_content_id, limit=limit
        )
        total = len(threads)
    else:
        threads = await svc.list_threads(user_id=user_id, limit=limit, offset=offset)
        total = await svc.count_threads(user_id=user_id)
    return ThreadListResponse(
        threads=threads,
        total=total,
    )


@router.get("/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread(
    thread_id: str,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """스레드 상세 조회.

    GET /api/threads/{thread_id}
    """
    thread = await svc.get_thread(thread_id, user_id=user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")

    return ThreadDetailResponse(
        thread_id=thread.thread_id,
        title=thread.title,
        messages=[m.to_dict() for m in thread.messages],
        created_at=thread.created_at,
        updated_at=thread.updated_at,
        content_id=str(thread.content_id) if thread.content_id else None,
    )


class UpdateThreadTitleRequest(BaseModel):
    """스레드 제목 업데이트 요청."""
    title: str = Field(..., min_length=1, description="새 제목")


@router.patch("/{thread_id}")
async def update_thread_title(
    thread_id: str,
    request: UpdateThreadTitleRequest,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """스레드 제목 업데이트.

    PATCH /api/threads/{thread_id}
    """
    thread = await svc.update_thread(thread_id, user_id=user_id, title=request.title)
    if not thread:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")
    return {"thread_id": thread.thread_id, "title": thread.title}


@router.patch("/{thread_id}/metadata")
async def update_thread_metadata(
    thread_id: str,
    request: UpdateThreadMetadataRequest,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """스레드 메타데이터 부분 업데이트 (병합).

    PATCH /api/threads/{thread_id}/metadata

    source_options, content_ids 등 클라이언트 상태 저장용.
    기존 metadata와 병합 (replace가 아닌 merge).
    """
    thread = await svc.update_thread_metadata(
        thread_id, user_id=user_id, metadata_patch=request.metadata
    )
    if not thread:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")

    return {"thread_id": thread.thread_id, "message": "메타데이터가 업데이트되었습니다"}


@router.delete("/{thread_id}")
async def delete_thread(
    thread_id: str,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """스레드 삭제.

    DELETE /api/threads/{thread_id}
    """
    deleted = await svc.delete_thread(thread_id, user_id=user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")

    return {"message": "스레드가 삭제되었습니다", "thread_id": thread_id}


class BulkDeleteThreadsRequest(BaseModel):
    thread_ids: list[str]


class BulkDeleteThreadsResponse(BaseModel):
    deleted_ids: list[str]
    skipped_ids: list[str]
    message: str


@router.post("/bulk-delete")
async def bulk_delete_threads(
    request: BulkDeleteThreadsRequest,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """다중 스레드 일괄 삭제.

    POST /api/threads/bulk-delete
    """
    deleted_ids: list[str] = []
    skipped_ids: list[str] = []

    for thread_id in request.thread_ids:
        deleted = await svc.delete_thread(thread_id, user_id=user_id)
        if deleted:
            deleted_ids.append(thread_id)
        else:
            skipped_ids.append(thread_id)

    return BulkDeleteThreadsResponse(
        deleted_ids=deleted_ids,
        skipped_ids=skipped_ids,
        message=f"{len(deleted_ids)}개 스레드가 삭제되었습니다.",
    )


@router.post("/{thread_id}/regenerate")
async def regenerate_response(
    thread_id: str,
    request: RegenerateRequest,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session, scope="function"),
):
    """Replace the last assistant response with a queued Agent Worker run."""
    thread = await svc.get_thread(thread_id, user_id=user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    agent_metadata = _build_agent_metadata(
        context=request.context,
        reasoning=request.reasoning,
        allow_remote=request.allow_remote,
    )
    await _validate_content_ownership(session, user_id, agent_metadata)

    last_user_query = await svc.remove_last_assistant_message(
        thread_id,
        user_id=user_id,
    )
    messages = await svc.get_messages(thread_id, user_id=user_id)
    last_user_message = messages[-1] if messages and messages[-1].role == "user" else None
    if not last_user_query or not last_user_message:
        raise HTTPException(status_code=400, detail="No assistant response to regenerate")

    assistant_message = await svc.add_message(
        thread_id,
        user_id=user_id,
        role="assistant",
        content="",
        status="queued",
        metadata={
            "mode": request.mode,
            "context": agent_metadata,
            "regenerated": True,
        },
    )
    await _commit_and_enqueue_agent(
        session=session,
        svc=svc,
        thread_id=thread_id,
        user_id=user_id,
        user_message_id=last_user_message.message_id,
        assistant_message_id=assistant_message.message_id,
    )

    return StreamingResponse(
        _relay_agent_events(assistant_message.message_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class CreateThreadRequest(BaseModel):
    """스레드+메시지 생성 요청 (AI 실행 없음)."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., description="사용자 질문", min_length=1)
    mode: str = Field(default="auto", description="AI 모드")
    context: dict | None = Field(default=None, description="추가 컨텍스트")
    reasoning: Literal["auto", "none", "low", "medium", "high"] = "auto"
    allow_remote: bool = False


class CreateThreadResponse(BaseModel):
    """스레드+메시지 생성 응답."""

    thread_id: str = Field(..., description="스레드 ID")
    message_id: str = Field(..., description="AI 메시지 ID (queued 상태)")
    user_message_id: str = Field(..., description="사용자 메시지 ID")


class AddMessageRequest(BaseModel):
    """기존 스레드에 메시지 추가 요청 (AI 실행 없음)."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., description="사용자 질문", min_length=1)
    mode: str = Field(default="auto", description="AI 모드")
    context: dict | None = Field(default=None, description="추가 컨텍스트")
    reasoning: Literal["auto", "none", "low", "medium", "high"] = "auto"
    allow_remote: bool = False


class AddMessageResponse(BaseModel):
    """메시지 추가 응답."""

    thread_id: str = Field(..., description="스레드 ID")
    message_id: str = Field(..., description="AI 메시지 ID (queued 상태)")
    user_message_id: str = Field(..., description="사용자 메시지 ID")


@router.post("", response_model=CreateThreadResponse)
async def create_thread(
    request: CreateThreadRequest,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session, scope="function"),
):
    """새 스레드 생성 (AI 실행 없음, 확인 후 라우팅용).

    POST /api/threads

    HomePage에서 호출:
    1. 스레드 생성
    2. 사용자 메시지 저장 (completed)
    3. AI 메시지 생성 (queued 상태)
    4. 응답: {thread_id, message_id, user_message_id}

    클라이언트는 응답 후 /chat/{thread_id}?messageId={message_id} 로 이동
    ChatPage에서 GET .../stream으로 SSE 연결
    """
    logger.info(
        f"[Thread] Create: user={user_id}, query='{request.query[:50]}...', mode={request.mode}"
    )
    agent_metadata = _build_agent_metadata(
        context=request.context,
        reasoning=request.reasoning,
        allow_remote=request.allow_remote,
    )
    await _validate_content_ownership(session, user_id, agent_metadata)

    try:
        # 1. 새 스레드 생성
        # content_id는 File.id (프론트엔드 공개 ID).
        # ai_thread.content_id FK는 content.id를 참조하므로 변환 필요.
        file_id_str = agent_metadata.get("content_id") if agent_metadata else None
        actual_content_id = None
        if file_id_str:
            try:
                from uuid import UUID as _UUID
                from ..repositories.content_repository import ContentRepository
                _repo = ContentRepository(session)
                _content = await _repo.get_by_file_id(_UUID(str(file_id_str)))
                if _content:
                    actual_content_id = _content.id
            except Exception as _e:
                logger.warning(f"[Thread] content_id 변환 실패 (file_id={file_id_str}): {_e}")

        thread = await svc.create_thread(
            user_id=user_id,
            content_id=actual_content_id,
            metadata=agent_metadata,
        )

        # 2. 사용자 메시지 저장 (completed)
        user_message = await svc.add_message(
            thread.thread_id,
            user_id=user_id,
            role="user",
            content=request.query,
            status="completed",
        )

        # 3. AI 응답 메시지 생성 (queued 상태)
        ai_message = await svc.add_message(
            thread.thread_id,
            user_id=user_id,
            role="assistant",
            content="",  # 빈 내용
            status="queued",  # queued 상태로 시작
            metadata={"mode": request.mode, "context": agent_metadata},
        )

        await _commit_and_enqueue_agent(
            session=session,
            svc=svc,
            thread_id=thread.thread_id,
            user_id=user_id,
            user_message_id=user_message.message_id,
            assistant_message_id=ai_message.message_id,
        )

        return CreateThreadResponse(
            thread_id=thread.thread_id,
            message_id=ai_message.message_id,
            user_message_id=user_message.message_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Thread] Create error: {e}")
        raise HTTPException(status_code=500, detail="Thread creation failed")


@router.post("/{thread_id}/messages", response_model=AddMessageResponse)
async def add_message(
    thread_id: str,
    request: AddMessageRequest,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session, scope="function"),
):
    """기존 스레드에 메시지 추가 (AI 실행 없음).

    POST /api/threads/{thread_id}/messages

    기존 스레드에서 추가 질문 시:
    1. 사용자 메시지 저장 (completed)
    2. AI 메시지 생성 (queued 상태)
    3. 응답: {thread_id, message_id, user_message_id}

    클라이언트는 응답 후 GET .../stream으로 SSE 연결
    """
    logger.info(
        f"[Thread] Add message: user={user_id}, thread_id={thread_id}, query='{request.query[:50]}...'"
    )
    agent_metadata = _build_agent_metadata(
        context=request.context,
        reasoning=request.reasoning,
        allow_remote=request.allow_remote,
    )

    # 스레드 존재 및 권한 확인
    thread = await svc.get_thread(thread_id, user_id=user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")
    await _validate_content_ownership(session, user_id, agent_metadata)

    try:
        # 1. 사용자 메시지 저장 (completed)
        user_message = await svc.add_message(
            thread_id,
            user_id=user_id,
            role="user",
            content=request.query,
            status="completed",
        )

        # 2. AI 응답 메시지 생성 (queued 상태)
        ai_message = await svc.add_message(
            thread_id,
            user_id=user_id,
            role="assistant",
            content="",
            status="queued",
            metadata={"mode": request.mode, "context": agent_metadata},
        )

        # 3. source_options가 있으면 thread metadata 자동 업데이트
        if agent_metadata and "source_options" in agent_metadata:
            try:
                await svc.update_thread_metadata(
                    thread_id,
                    user_id=user_id,
                    metadata_patch={"source_options": agent_metadata["source_options"]},
                )
            except Exception as meta_err:
                logger.warning(
                    f"[Thread] source_options metadata 업데이트 실패 (thread_id={thread_id}): {meta_err}"
                )

        await _commit_and_enqueue_agent(
            session=session,
            svc=svc,
            thread_id=thread_id,
            user_id=user_id,
            user_message_id=user_message.message_id,
            assistant_message_id=ai_message.message_id,
        )

        return AddMessageResponse(
            thread_id=thread_id,
            message_id=ai_message.message_id,
            user_message_id=user_message.message_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[Thread] Add message error: {e}")
        raise HTTPException(status_code=500, detail="Message creation failed")


@router.get("/{thread_id}/messages/{message_id}/stream")
async def stream_message_v2(
    thread_id: str,
    message_id: str,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """Relay Agent Worker Pub/Sub events and repair missed events from PostgreSQL."""
    thread = await svc.get_thread(thread_id, user_id=user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    messages = await svc.get_messages(thread_id, user_id=user_id)
    message = next((item for item in messages if item.message_id == message_id), None)
    if not message or message.role != "assistant":
        raise HTTPException(status_code=404, detail="Assistant message not found")

    return StreamingResponse(
        _relay_agent_events(message_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
