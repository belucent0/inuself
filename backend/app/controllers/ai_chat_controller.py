"""AI 채팅 API 엔드포인트 (LangGraph 기반).

V8.0: LangGraph 워크플로우를 사용한 AI 모드 채팅 API.
V8.5: conversation_id → thread_id 용어 통일
V8.6: RESTful API 구조 개선 - /api/threads로 통일
V9.0: Phase 1 - user_id 기반 스레드 관리, DB 영속화
V9.1: 백그라운드 AI 응답 생성 - 클라이언트 연결 끊김에도 서버에서 계속 생성

기존 chat_controller.py와 별도로 동작합니다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings, Settings
from ..db.session import get_session, async_session_factory
from ..agents import run_ai_agent
from ..agents.graph import stream_ai_agent
from ..services.thread_service import (
    get_thread_service,
    ThreadService,
)

router = APIRouter(prefix="/api/threads", tags=["threads"])


class CreateThreadRequest(BaseModel):
    """새 스레드 생성 요청 (첫 메시지 포함)."""

    query: str = Field(..., description="사용자 질문", min_length=1)
    mode: str = Field(
        default="auto",
        description="AI 모드 (auto, simple, search, rag, reasoning, hybrid)",
    )
    context: dict | None = Field(
        default=None, description="추가 컨텍스트 (RAG용 content_ids 등)"
    )


class AddMessageRequest(BaseModel):
    """기존 스레드에 메시지 추가 요청."""

    query: str = Field(..., description="사용자 질문", min_length=1)
    mode: str = Field(
        default="auto",
        description="AI 모드 (auto, simple, search, rag, reasoning, hybrid)",
    )
    context: dict | None = Field(
        default=None, description="추가 컨텍스트 (RAG용 content_ids 등)"
    )
    skip_user_message: bool = Field(
        default=False, description="사용자 메시지 저장 건너뛰기 (이미 저장된 경우)"
    )


class RegenerateRequest(BaseModel):
    """답변 재생성 요청."""

    mode: str = Field(
        default="auto",
        description="AI 모드 (auto, simple, search, rag, reasoning, hybrid)",
    )
    context: dict | None = Field(default=None, description="추가 컨텍스트")


class CitationModel(BaseModel):
    """Citation (출처 표시) 모델 - Phase 4."""

    id: int = Field(..., description="출처 번호")
    title: str = Field(..., description="출처 제목")
    url: str = Field(..., description="출처 URL")
    snippet: str = Field(default="", description="인용된 부분")
    verified: bool = Field(default=True, description="검증 여부")


class ThreadResponse(BaseModel):
    """스레드 응답 (생성/메시지 추가 후)."""

    response: str = Field(..., description="AI 응답")
    thread_id: str = Field(..., description="스레드 ID")
    mode: str = Field(..., description="사용된 AI 모드")
    sources: list[dict] = Field(default=[], description="참조 소스 목록")
    citations: list[CitationModel] = Field(
        default=[], description="출처 표시 목록 (Phase 4)"
    )
    thinking_steps: list[dict] = Field(default=[], description="사고 과정")


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


# === 의존성 ===


async def get_svc(session: AsyncSession = Depends(get_session)) -> ThreadService:
    """스레드 서비스 의존성 (DB 세션 포함)."""
    return get_thread_service(session)


# TODO: 실제 인증 구현 시 교체
async def get_current_user_id() -> UUID:
    """현재 사용자 ID 반환 (임시 구현)."""
    return UUID("01234567-89ab-cdef-0123-456789abcdef")


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


# === 백그라운드 AI 응답 생성 ===


async def generate_ai_response_background(
    thread_id: str,
    user_id: UUID,
    query: str,
    mode: str,
    context: dict | None,
    settings: Settings,
    message_id: str | None = None,
) -> None:
    """백그라운드에서 AI 응답을 생성하고 저장합니다.

    클라이언트 연결이 끊어져도 서버에서 AI 응답 생성을 계속하고,
    완료되면 DB에 저장합니다. 나중에 스레드를 조회하면 생성된 응답을 볼 수 있습니다.

    V9.2: message_id가 제공되면 기존 메시지를 업데이트, 없으면 새로 생성
    """
    logger.info(
        f"[Thread] Background generation started: thread_id={thread_id}, message_id={message_id}, query='{query[:50]}...'"
    )

    try:
        # 새로운 DB 세션 생성 (백그라운드 태스크는 별도 세션 필요)
        async with async_session_factory() as session:
            svc = get_thread_service(session)

            # AI Agent 실행 (비스트리밍)
            result = await run_ai_agent(
                settings=settings,
                query=query,
                thread_id=thread_id,
                mode=mode,
                metadata=context,
                enable_retry=True,
                max_retries=3,
            )

            response_content = result.get("response", "")

            if message_id:
                # V9.2: 기존 메시지가 있으면 상태 업데이트 (generating → completed)
                await svc.update_message_status(
                    message_id,
                    status="completed",
                    content=response_content,
                )
                logger.info(
                    f"[Thread] Background generation updated existing message: message_id={message_id}"
                )
            else:
                # 기존 방식: 새 메시지 생성
                await svc.add_message(
                    thread_id,
                    user_id=user_id,
                    role="assistant",
                    content=response_content,
                    metadata={
                        "mode": _normalize_mode_name(result.get("mode", "simple")),
                        "sources": result.get("sources", []),
                        "citations": result.get("citations", []),
                        "intent": result.get("query_analysis"),
                        "search_queries": result.get("search_queries", []),
                        "search_results": result.get("search_results", []),
                        "thinking_steps": result.get("thinking_steps", []),
                        "search_retry_count": result.get("search_retry_count", 0),
                        "search_quality_score": result.get("search_quality_score", 0.0),
                        "failed_queries": result.get("failed_queries", []),
                        "retry_reason": result.get("retry_reason", ""),
                        "background_generated": True,  # 백그라운드 생성 플래그
                    },
                    status="completed",
                )

            # 백그라운드 태스크에서는 명시적 커밋 필요 (FastAPI 의존성 외부)
            await session.commit()

            logger.info(
                f"[Thread] Background generation completed: thread_id={thread_id}"
            )

    except Exception as e:
        logger.exception(
            f"[Thread] Background generation failed: thread_id={thread_id}, error={e}"
        )
        # 에러 발생 시 메시지 상태를 failed로 업데이트
        try:
            async with async_session_factory() as session:
                svc = get_thread_service(session)

                if message_id:
                    # 기존 메시지 상태를 failed로 업데이트
                    await svc.update_message_status(
                        message_id,
                        status="failed",
                        content=f"죄송합니다. 응답 생성 중 오류가 발생했습니다: {str(e)}",
                    )
                else:
                    # 새 에러 메시지 생성
                    await svc.add_message(
                        thread_id,
                        user_id=user_id,
                        role="assistant",
                        content=f"죄송합니다. 응답 생성 중 오류가 발생했습니다: {str(e)}",
                        metadata={
                            "error": True,
                            "error_message": str(e),
                            "background_generated": True,
                        },
                        status="failed",
                    )
                await session.commit()  # 명시적 커밋
        except Exception as save_error:
            logger.exception(f"[Thread] Failed to save error message: {save_error}")


@router.post("", response_model=ThreadResponse)
async def create_thread(
    request: CreateThreadRequest,
    settings: Settings = Depends(get_settings),
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """새 스레드 생성 (첫 메시지 포함, 비스트리밍).

    POST /api/threads - 새 대화 시작
    """
    logger.info(
        f"[Thread] Create: user={user_id}, query='{request.query[:50]}...', mode={request.mode}"
    )

    try:
        # 새 스레드 생성 (user_id 포함)
        thread = await svc.create_thread(user_id=user_id, metadata=request.context)

        # 사용자 메시지 저장
        await svc.add_message(
            thread.thread_id,
            user_id=user_id,
            role="user",
            content=request.query,
        )

        # AI Agent 실행 (V8.4: 재시도 기능 활성화)
        result = await run_ai_agent(
            settings=settings,
            query=request.query,
            thread_id=thread.thread_id,
            mode=request.mode,
            metadata=request.context,
            enable_retry=True,  # V8.4: 검색 재시도 활성화
            max_retries=3,
        )

        # AI 응답 저장 (V8.4: 재시도 메타데이터 포함)
        await svc.add_message(
            thread.thread_id,
            user_id=user_id,
            role="assistant",
            content=result.get("response", ""),
            metadata={
                "mode": _normalize_mode_name(result.get("mode", "simple")),
                "sources": result.get("sources", []),
                "citations": result.get("citations", []),  # Phase 4
                "intent": result.get("query_analysis"),  # Intent Parser 결과
                "search_queries": result.get("search_queries", []),  # 생성된 검색 쿼리
                "search_results": result.get(
                    "search_results", []
                ),  # 검색 결과 (품질 점수 포함)
                "thinking_steps": result.get("thinking_steps", []),  # 사고 과정
                # V8.4: 재시도 정보
                "search_retry_count": result.get("search_retry_count", 0),
                "search_quality_score": result.get("search_quality_score", 0.0),
                "failed_queries": result.get("failed_queries", []),
                "retry_reason": result.get("retry_reason", ""),
            },
        )

        return ThreadResponse(
            response=result.get("response", ""),
            thread_id=thread.thread_id,
            mode=_normalize_mode_name(result.get("mode", "simple")),
            sources=result.get("sources", []),
            citations=result.get("citations", []),  # Phase 4: Citation 추가
            thinking_steps=result.get("thinking_steps", []),
        )

    except Exception as e:
        logger.exception(f"[Thread] Create error: {e}")
        raise HTTPException(status_code=500, detail=f"AI 처리 실패: {str(e)}")


@router.post("/stream")
async def create_thread_stream(
    request: CreateThreadRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """새 스레드 생성 (첫 메시지 포함, SSE 스트리밍).

    POST /api/threads/stream - 새 대화 시작 (스트리밍)

    V9.1: 클라이언트 연결 끊김 시 백그라운드에서 응답 생성 계속
    V9.2: 메시지 상태 관리 - generating → completed
    """
    logger.info(
        f"[Thread] Create stream: user={user_id}, query='{request.query[:50]}...', mode={request.mode}"
    )

    # 스레드 먼저 생성 (generate 함수 밖에서)
    thread = await svc.create_thread(user_id=user_id, metadata=request.context)

    # 사용자 메시지 저장 (완료 상태)
    await svc.add_message(
        thread.thread_id,
        user_id=user_id,
        role="user",
        content=request.query,
        status="completed",
    )

    # AI 응답 메시지를 "generating" 상태로 미리 생성
    ai_message = await svc.add_message(
        thread.thread_id,
        user_id=user_id,
        role="assistant",
        content="",  # 빈 내용으로 시작
        status="generating",
    )

    # 클로저에서 사용할 변수 캡처
    captured_user_id = user_id
    captured_thread_id = thread.thread_id
    captured_query = request.query
    captured_mode = request.mode
    captured_context = request.context
    captured_settings = settings
    captured_ai_message_id = ai_message.message_id

    # 응답 완료 여부 추적
    response_completed = {"value": False}

    async def generate():
        nonlocal response_completed
        try:
            # 스레드 ID 먼저 전송
            yield f"data: {json.dumps({'type': 'thread_id', 'data': captured_thread_id})}\n\n"
            # AI 메시지 ID도 전송 (스트림 재연결용)
            yield f"data: {json.dumps({'type': 'message_id', 'data': captured_ai_message_id})}\n\n"

            # AI Agent 스트리밍 실행 (V8.4: 재시도 메타데이터 수집)
            full_response = ""
            mode_used = "simple"
            sources = []
            citations = []
            intent = None
            search_queries = []
            search_results = []
            thinking_steps = []
            # V8.4: 재시도 정보
            search_retry_count = 0
            search_quality_score = 0.0
            failed_queries = []
            retry_reason = ""

            async for event in stream_ai_agent(
                settings=captured_settings,
                query=captured_query,
                thread_id=captured_thread_id,
                mode=captured_mode,
                metadata=captured_context,
                enable_retry=True,  # V8.4: 재시도 활성화
                max_retries=3,
                user_id=str(captured_user_id),
            ):
                event_type = event.get("type", "")
                event_data = event.get("data")

                # 이벤트 타입에 따른 처리
                if event_type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'data': event_data})}\n\n"
                    # thinking_steps에 추가하여 DB 저장용으로 수집
                    if isinstance(event_data, dict):
                        thinking_steps.append(event_data)
                        if "mode" in event_data:
                            mode_used = _normalize_mode_name(event_data["mode"])
                        # V8.4: 재시도 정보 수집
                        if "search_retry_count" in event_data:
                            search_retry_count = event_data["search_retry_count"]
                        if "search_quality_score" in event_data:
                            search_quality_score = event_data["search_quality_score"]
                        if "retry_reason" in event_data:
                            retry_reason = event_data["retry_reason"]
                        # H-2: thinking_steps 주기적 저장 (3개마다)
                        if len(thinking_steps) % 3 == 0:
                            await svc.update_message_metadata(
                                captured_ai_message_id,
                                thinking_steps=thinking_steps,
                            )

                elif event_type == "query_analysis":
                    # 쿼리 재정의 결과 전송 (Perplexity 스타일 UI용)
                    intent = event_data  # 저장용
                    # H-2: intent 즉시 DB 저장
                    await svc.update_message_metadata(
                        captured_ai_message_id,
                        intent=intent,
                    )
                    yield f"data: {json.dumps({'type': 'query_analysis', 'data': event_data})}\n\n"

                elif event_type == "token":
                    # 토큰 단위 스트리밍 - 점진적으로 응답 축적
                    token = (
                        event_data
                        if isinstance(event_data, str)
                        else str(event_data or "")
                    )
                    full_response += token
                    yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"

                elif event_type == "content":
                    # 전체 콘텐츠 업데이트 (non-streaming fallback)
                    full_response = (
                        event_data if isinstance(event_data, str) else str(event_data)
                    )
                    yield f"data: {json.dumps({'type': 'content', 'data': full_response})}\n\n"

                elif event_type == "sources":
                    sources = event_data if isinstance(event_data, list) else []
                    # H-2: sources 즉시 DB 저장 (연결 끊김에도 보존)
                    await svc.update_message_metadata(
                        captured_ai_message_id,
                        sources=sources,
                        mode=mode_used,
                    )
                    yield f"data: {json.dumps({'type': 'sources', 'data': sources})}\n\n"

                elif event_type == "citations":  # Phase 4
                    citations = event_data if isinstance(event_data, list) else []
                    # H-2: citations 즉시 DB 저장
                    await svc.update_message_metadata(
                        captured_ai_message_id,
                        citations=citations,
                    )
                    yield f"data: {json.dumps({'type': 'citations', 'data': citations})}\n\n"

                elif event_type == "search_queries":  # V8.3
                    search_queries = event_data if isinstance(event_data, list) else []
                    # H-2: search_queries 즉시 DB 저장
                    await svc.update_message_metadata(
                        captured_ai_message_id,
                        search_queries=search_queries,
                    )
                    yield f"data: {json.dumps({'type': 'search_queries', 'data': search_queries})}\n\n"

                elif event_type == "search_results":  # V8.3
                    search_results = event_data if isinstance(event_data, list) else []
                    # H-2: search_results 즉시 DB 저장
                    await svc.update_message_metadata(
                        captured_ai_message_id,
                        search_results=search_results,
                    )
                    yield f"data: {json.dumps({'type': 'search_results', 'data': search_results})}\n\n"

                elif event_type == "search_retry":  # V8.4: 재시도 이벤트
                    # 재시도 정보 업데이트
                    if isinstance(event_data, dict):
                        search_retry_count = event_data.get(
                            "retry_count", search_retry_count
                        )
                        search_quality_score = event_data.get(
                            "quality_score", search_quality_score
                        )
                        retry_reason = event_data.get("reason", retry_reason)
                        if "failed_query" in event_data:
                            failed_queries.append(event_data["failed_query"])
                    # 클라이언트에 재시도 정보 전송
                    yield f"data: {json.dumps({'type': 'search_retry', 'data': event_data})}\n\n"

                elif event_type == "done":
                    # V9.2: AI 응답 완료 - generating → completed 상태로 업데이트
                    if full_response:
                        # 기존에 생성한 메시지의 상태, 내용, 메타데이터 업데이트
                        await svc.update_message_status(
                            captured_ai_message_id,
                            status="completed",
                            content=full_response,
                            metadata={
                                "mode": mode_used,
                                "sources": sources,
                                "citations": citations,
                                "intent": intent,
                                "search_queries": search_queries,
                                "search_results": search_results,
                                "thinking_steps": thinking_steps,
                                "search_retry_count": search_retry_count,
                                "search_quality_score": search_quality_score,
                                "failed_queries": failed_queries,
                                "retry_reason": retry_reason,
                            },
                        )
                    response_completed["value"] = True
                    yield f"data: {json.dumps({'type': 'done', 'data': None})}\n\n"

                elif event_type == "error":
                    # 에러 시 failed 상태로 업데이트
                    await svc.update_message_status(
                        captured_ai_message_id,
                        status="failed",
                        content=f"오류 발생: {str(event_data)}",
                    )
                    yield f"data: {json.dumps({'type': 'error', 'data': str(event_data)})}\n\n"

        except asyncio.CancelledError:
            # 클라이언트 연결 끊김 - 백그라운드에서 계속 생성
            logger.info(
                f"[Thread] Client disconnected, continuing in background: thread_id={captured_thread_id}, message_id={captured_ai_message_id}"
            )
            if not response_completed["value"]:
                # 백그라운드 태스크로 응답 생성 계속
                asyncio.create_task(
                    generate_ai_response_background(
                        thread_id=captured_thread_id,
                        user_id=captured_user_id,
                        query=captured_query,
                        mode=captured_mode,
                        context=captured_context,
                        settings=captured_settings,
                        message_id=captured_ai_message_id,  # 기존 메시지 ID 전달
                    )
                )
            raise  # CancelledError 다시 발생시켜 정상 종료

        except Exception as e:
            logger.exception(f"[Thread] Create stream error: {e}")
            # 에러 시 메시지 상태를 failed로 업데이트
            await svc.update_message_status(
                captured_ai_message_id,
                status="failed",
                content=f"스트리밍 오류: {str(e)}",
            )
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("", response_model=ThreadListResponse)
async def list_threads(
    limit: int = 20,
    offset: int = 0,
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """스레드 목록 조회.

    GET /api/threads
    """
    threads = await svc.list_threads(user_id=user_id, limit=limit, offset=offset)
    return ThreadListResponse(
        threads=threads,
        total=len(threads),
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
    )


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


@router.post("/{thread_id}/messages", response_model=ThreadResponse)
async def add_message(
    thread_id: str,
    request: AddMessageRequest,
    settings: Settings = Depends(get_settings),
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """기존 스레드에 메시지 추가 (비스트리밍).

    POST /api/threads/{thread_id}/messages
    """
    logger.info(
        f"[Thread] Add message: user={user_id}, thread_id={thread_id}, query='{request.query[:50]}...', mode={request.mode}"
    )

    # 스레드 존재 확인 (권한 검증 포함)
    thread = await svc.get_thread(thread_id, user_id=user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")

    try:
        # 사용자 메시지 저장
        await svc.add_message(
            thread_id,
            user_id=user_id,
            role="user",
            content=request.query,
        )

        # AI Agent 실행
        result = await run_ai_agent(
            settings=settings,
            query=request.query,
            thread_id=thread_id,
            mode=request.mode,
            metadata=request.context,
            enable_retry=True,
            max_retries=3,
        )

        # AI 응답 저장
        await svc.add_message(
            thread_id,
            user_id=user_id,
            role="assistant",
            content=result.get("response", ""),
            metadata={
                "mode": _normalize_mode_name(result.get("mode", "simple")),
                "sources": result.get("sources", []),
                "citations": result.get("citations", []),
                "intent": result.get("query_analysis"),
                "search_queries": result.get("search_queries", []),
                "search_results": result.get("search_results", []),
                "thinking_steps": result.get("thinking_steps", []),
                "search_retry_count": result.get("search_retry_count", 0),
                "search_quality_score": result.get("search_quality_score", 0.0),
                "failed_queries": result.get("failed_queries", []),
                "retry_reason": result.get("retry_reason", ""),
            },
        )

        return ThreadResponse(
            response=result.get("response", ""),
            thread_id=thread_id,
            mode=_normalize_mode_name(result.get("mode", "simple")),
            sources=result.get("sources", []),
            citations=result.get("citations", []),
            thinking_steps=result.get("thinking_steps", []),
        )

    except Exception as e:
        logger.exception(f"[Thread] Add message error: {e}")
        raise HTTPException(status_code=500, detail=f"AI 처리 실패: {str(e)}")


@router.post("/{thread_id}/messages/stream")
async def add_message_stream(
    thread_id: str,
    request: AddMessageRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """기존 스레드에 메시지 추가 (SSE 스트리밍).

    POST /api/threads/{thread_id}/messages/stream

    V9.1: 클라이언트 연결 끊김 시 백그라운드에서 응답 생성 계속
    V9.2: 메시지 상태 관리 - generating → completed
    """
    logger.info(
        f"[Thread] Add message stream: user={user_id}, thread_id={thread_id}, query='{request.query[:50]}...', mode={request.mode}"
    )

    # 스레드 존재 확인 (권한 검증 포함)
    thread = await svc.get_thread(thread_id, user_id=user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")

    # 사용자 메시지 저장 (skip_user_message가 False일 때만)
    # HomePage에서 이미 저장된 경우 skip_user_message=True로 중복 저장 방지
    if not request.skip_user_message:
        await svc.add_message(
            thread_id,
            user_id=user_id,
            role="user",
            content=request.query,
            status="completed",
        )

    # V9.2: AI 응답 메시지를 "generating" 상태로 미리 생성
    ai_message = await svc.add_message(
        thread_id,
        user_id=user_id,
        role="assistant",
        content="",  # 빈 내용으로 시작
        status="generating",
    )

    # 클로저에서 사용할 변수 캡처
    captured_user_id = user_id
    captured_thread_id = thread_id
    captured_query = request.query
    captured_mode = request.mode
    captured_context = request.context
    captured_settings = settings
    captured_ai_message_id = ai_message.message_id

    # 응답 완료 여부 추적
    response_completed = {"value": False}

    async def generate():
        nonlocal response_completed
        try:
            # 스레드 ID 전송
            yield f"data: {json.dumps({'type': 'thread_id', 'data': captured_thread_id})}\n\n"
            # AI 메시지 ID도 전송 (스트림 재연결용)
            yield f"data: {json.dumps({'type': 'message_id', 'data': captured_ai_message_id})}\n\n"

            # AI Agent 스트리밍 실행
            full_response = ""
            mode_used = _normalize_mode_name(request.mode)
            sources = []
            citations = []
            intent = None
            search_queries = []
            search_results = []
            thinking_steps = []
            search_retry_count = 0
            search_quality_score = 0.0
            failed_queries = []
            retry_reason = ""

            async for event in stream_ai_agent(
                settings=captured_settings,
                query=captured_query,
                thread_id=captured_thread_id,
                mode=captured_mode,
                metadata=captured_context,
                enable_retry=True,
                max_retries=3,
                user_id=str(captured_user_id),
            ):
                event_type = event.get("type", "")
                event_data = event.get("data")

                if event_type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'data': event_data})}\n\n"
                    # thinking_steps에 추가하여 DB 저장용으로 수집
                    if isinstance(event_data, dict):
                        thinking_steps.append(event_data)
                        if "mode" in event_data:
                            mode_used = _normalize_mode_name(event_data["mode"])
                        if "search_retry_count" in event_data:
                            search_retry_count = event_data["search_retry_count"]
                        if "search_quality_score" in event_data:
                            search_quality_score = event_data["search_quality_score"]
                        if "retry_reason" in event_data:
                            retry_reason = event_data["retry_reason"]
                        # H-2: thinking_steps 주기적 저장 (3개마다)
                        if len(thinking_steps) % 3 == 0:
                            await svc.update_message_metadata(
                                captured_ai_message_id,
                                thinking_steps=thinking_steps,
                            )

                elif event_type == "query_analysis":
                    intent = event_data
                    # H-2: intent 즉시 DB 저장
                    await svc.update_message_metadata(
                        captured_ai_message_id,
                        intent=intent,
                    )
                    yield f"data: {json.dumps({'type': 'query_analysis', 'data': event_data})}\n\n"

                elif event_type == "token":
                    token = (
                        event_data
                        if isinstance(event_data, str)
                        else str(event_data or "")
                    )
                    full_response += token
                    yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"

                elif event_type == "content":
                    full_response = (
                        event_data if isinstance(event_data, str) else str(event_data)
                    )
                    yield f"data: {json.dumps({'type': 'content', 'data': full_response})}\n\n"

                elif event_type == "sources":
                    sources = event_data if isinstance(event_data, list) else []
                    # H-2: sources 즉시 DB 저장 (연결 끊김에도 보존)
                    await svc.update_message_metadata(
                        captured_ai_message_id,
                        sources=sources,
                        mode=mode_used,
                    )
                    yield f"data: {json.dumps({'type': 'sources', 'data': sources})}\n\n"

                elif event_type == "citations":
                    citations = event_data if isinstance(event_data, list) else []
                    # H-2: citations 즉시 DB 저장
                    await svc.update_message_metadata(
                        captured_ai_message_id,
                        citations=citations,
                    )
                    yield f"data: {json.dumps({'type': 'citations', 'data': citations})}\n\n"

                elif event_type == "search_queries":
                    search_queries = event_data if isinstance(event_data, list) else []
                    # H-2: search_queries 즉시 DB 저장
                    await svc.update_message_metadata(
                        captured_ai_message_id,
                        search_queries=search_queries,
                    )
                    yield f"data: {json.dumps({'type': 'search_queries', 'data': search_queries})}\n\n"

                elif event_type == "search_results":
                    search_results = event_data if isinstance(event_data, list) else []
                    # H-2: search_results 즉시 DB 저장
                    await svc.update_message_metadata(
                        captured_ai_message_id,
                        search_results=search_results,
                    )
                    yield f"data: {json.dumps({'type': 'search_results', 'data': search_results})}\n\n"

                elif event_type == "search_retry":
                    if isinstance(event_data, dict):
                        search_retry_count = event_data.get(
                            "retry_count", search_retry_count
                        )
                        search_quality_score = event_data.get(
                            "quality_score", search_quality_score
                        )
                        retry_reason = event_data.get("reason", retry_reason)
                        if "failed_query" in event_data:
                            failed_queries.append(event_data["failed_query"])
                    yield f"data: {json.dumps({'type': 'search_retry', 'data': event_data})}\n\n"

                elif event_type == "done":
                    # V9.2: AI 응답 완료 - generating → completed 상태로 업데이트
                    if full_response:
                        await svc.update_message_status(
                            captured_ai_message_id,
                            status="completed",
                            content=full_response,
                            metadata={
                                "mode": mode_used,
                                "sources": sources,
                                "citations": citations,
                                "intent": intent,
                                "search_queries": search_queries,
                                "search_results": search_results,
                                "thinking_steps": thinking_steps,
                                "search_retry_count": search_retry_count,
                                "search_quality_score": search_quality_score,
                                "failed_queries": failed_queries,
                                "retry_reason": retry_reason,
                            },
                        )
                    response_completed["value"] = True
                    yield f"data: {json.dumps({'type': 'done', 'data': None})}\n\n"

                elif event_type == "error":
                    # 에러 시 failed 상태로 업데이트
                    await svc.update_message_status(
                        captured_ai_message_id,
                        status="failed",
                        content=f"오류 발생: {str(event_data)}",
                    )
                    yield f"data: {json.dumps({'type': 'error', 'data': str(event_data)})}\n\n"

        except asyncio.CancelledError:
            # 클라이언트 연결 끊김 - 백그라운드에서 계속 생성
            logger.info(
                f"[Thread] Client disconnected, continuing in background: thread_id={captured_thread_id}, message_id={captured_ai_message_id}"
            )
            if not response_completed["value"]:
                # 백그라운드 태스크로 응답 생성 계속
                asyncio.create_task(
                    generate_ai_response_background(
                        thread_id=captured_thread_id,
                        user_id=captured_user_id,
                        query=captured_query,
                        mode=captured_mode,
                        context=captured_context,
                        settings=captured_settings,
                        message_id=captured_ai_message_id,  # 기존 메시지 ID 전달
                    )
                )
            raise  # CancelledError 다시 발생시켜 정상 종료

        except Exception as e:
            logger.exception(f"[Thread] Add message stream error: {e}")
            # 에러 시 메시지 상태를 failed로 업데이트
            await svc.update_message_status(
                captured_ai_message_id,
                status="failed",
                content=f"스트리밍 오류: {str(e)}",
            )
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{thread_id}/regenerate")
async def regenerate_response(
    thread_id: str,
    request: RegenerateRequest,
    settings: Settings = Depends(get_settings),
    svc: ThreadService = Depends(get_svc),
    user_id: UUID = Depends(get_current_user_id),
):
    """마지막 AI 답변 재생성 (SSE 스트리밍).

    POST /api/threads/{thread_id}/regenerate

    1. 마지막 assistant 메시지 삭제
    2. 마지막 user 메시지로 AI 재요청
    """
    logger.info(
        f"[Thread] Regenerate: user={user_id}, thread_id={thread_id}, mode={request.mode}"
    )

    # 스레드 존재 확인 (권한 검증 포함)
    thread = await svc.get_thread(thread_id, user_id=user_id)
    if not thread:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")

    # 마지막 assistant 메시지 삭제하고 user 쿼리 가져오기
    last_user_query = await svc.remove_last_assistant_message(
        thread_id, user_id=user_id
    )
    if not last_user_query:
        raise HTTPException(
            status_code=400,
            detail="재생성할 답변이 없습니다 (마지막 메시지가 assistant가 아님)",
        )

    # 클로저에서 사용할 변수 캡처
    captured_user_id = user_id

    async def generate():
        try:
            # AI Agent 스트리밍 실행
            full_response = ""
            mode_used = _normalize_mode_name(request.mode or "auto")
            sources = []
            citations = []
            intent = None
            search_queries = []
            search_results = []
            thinking_steps = []
            search_retry_count = 0
            search_quality_score = 0.0
            failed_queries = []
            retry_reason = ""

            async for event in stream_ai_agent(
                settings=settings,
                query=last_user_query,
                thread_id=thread_id,
                mode=request.mode,
                metadata=request.context,
                enable_retry=True,
                max_retries=3,
                user_id=str(captured_user_id),
            ):
                event_type = event.get("type", "")
                event_data = event.get("data")

                if event_type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'data': event_data})}\n\n"
                    # thinking_steps에 추가하여 DB 저장용으로 수집
                    if isinstance(event_data, dict):
                        thinking_steps.append(event_data)
                        if "mode" in event_data:
                            mode_used = _normalize_mode_name(event_data["mode"])
                        if "search_retry_count" in event_data:
                            search_retry_count = event_data["search_retry_count"]
                        if "search_quality_score" in event_data:
                            search_quality_score = event_data["search_quality_score"]
                        if "retry_reason" in event_data:
                            retry_reason = event_data["retry_reason"]

                elif event_type == "query_analysis":
                    intent = event_data
                    yield f"data: {json.dumps({'type': 'query_analysis', 'data': event_data})}\n\n"

                elif event_type == "token":
                    token = (
                        event_data
                        if isinstance(event_data, str)
                        else str(event_data or "")
                    )
                    full_response += token
                    yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"

                elif event_type == "content":
                    full_response = (
                        event_data if isinstance(event_data, str) else str(event_data)
                    )
                    yield f"data: {json.dumps({'type': 'content', 'data': full_response})}\n\n"

                elif event_type == "sources":
                    sources = event_data if isinstance(event_data, list) else []
                    yield f"data: {json.dumps({'type': 'sources', 'data': sources})}\n\n"

                elif event_type == "citations":
                    citations = event_data if isinstance(event_data, list) else []
                    yield f"data: {json.dumps({'type': 'citations', 'data': citations})}\n\n"

                elif event_type == "search_queries":
                    search_queries = event_data if isinstance(event_data, list) else []
                    yield f"data: {json.dumps({'type': 'search_queries', 'data': search_queries})}\n\n"

                elif event_type == "search_results":
                    search_results = event_data if isinstance(event_data, list) else []
                    yield f"data: {json.dumps({'type': 'search_results', 'data': search_results})}\n\n"

                elif event_type == "search_retry":
                    if isinstance(event_data, dict):
                        search_retry_count = event_data.get(
                            "retry_count", search_retry_count
                        )
                        search_quality_score = event_data.get(
                            "quality_score", search_quality_score
                        )
                        retry_reason = event_data.get("reason", retry_reason)
                        if "failed_query" in event_data:
                            failed_queries.append(event_data["failed_query"])
                    yield f"data: {json.dumps({'type': 'search_retry', 'data': event_data})}\n\n"

                elif event_type == "done":
                    if full_response:
                        await svc.add_message(
                            thread_id,
                            user_id=captured_user_id,
                            role="assistant",
                            content=full_response,
                            metadata={
                                "mode": mode_used,
                                "sources": sources,
                                "citations": citations,
                                "intent": intent,
                                "search_queries": search_queries,
                                "search_results": search_results,
                                "thinking_steps": thinking_steps,
                                "search_retry_count": search_retry_count,
                                "search_quality_score": search_quality_score,
                                "failed_queries": failed_queries,
                                "retry_reason": retry_reason,
                                "regenerated": True,  # 재생성 플래그
                            },
                        )
                    yield f"data: {json.dumps({'type': 'done', 'data': None})}\n\n"

                elif event_type == "error":
                    yield f"data: {json.dumps({'type': 'error', 'data': str(event_data)})}\n\n"

        except Exception as e:
            logger.exception(f"[Thread] Regenerate error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
