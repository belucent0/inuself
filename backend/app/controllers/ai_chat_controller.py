"""AI 채팅 API 엔드포인트 (LangGraph 기반).

V8.0: LangGraph 워크플로우를 사용한 AI 모드 채팅 API.
V8.5: conversation_id → thread_id 용어 통일
V8.6: RESTful API 구조 개선 - /api/threads로 통일

기존 chat_controller.py와 별도로 동작합니다.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from ..core.config import get_settings, Settings
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
    mode: str = Field(default="auto", description="AI 모드 (auto, simple, search, rag, reasoning, hybrid)")
    context: dict | None = Field(default=None, description="추가 컨텍스트 (RAG용 content_ids 등)")


class AddMessageRequest(BaseModel):
    """기존 스레드에 메시지 추가 요청."""
    query: str = Field(..., description="사용자 질문", min_length=1)
    mode: str = Field(default="auto", description="AI 모드 (auto, simple, search, rag, reasoning, hybrid)")
    context: dict | None = Field(default=None, description="추가 컨텍스트 (RAG용 content_ids 등)")


class RegenerateRequest(BaseModel):
    """답변 재생성 요청."""
    mode: str = Field(default="auto", description="AI 모드 (auto, simple, search, rag, reasoning, hybrid)")
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
    citations: list[CitationModel] = Field(default=[], description="출처 표시 목록 (Phase 4)")
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


def get_svc() -> ThreadService:
    """스레드 서비스 의존성."""
    return get_thread_service()


@router.post("", response_model=ThreadResponse)
async def create_thread(
    request: CreateThreadRequest,
    settings: Settings = Depends(get_settings),
    svc: ThreadService = Depends(get_svc),
):
    """새 스레드 생성 (첫 메시지 포함, 비스트리밍).

    POST /api/threads - 새 대화 시작
    """
    logger.info(f"[Thread] Create: query='{request.query[:50]}...', mode={request.mode}")

    try:
        # 새 스레드 생성
        thread = await svc.create_thread(metadata=request.context)

        # 사용자 메시지 저장
        await svc.add_message(
            thread.thread_id,
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
            role="assistant",
            content=result.get("response", ""),
            metadata={
                "mode": str(result.get("mode", "simple")),
                "sources": result.get("sources", []),
                "citations": result.get("citations", []),  # Phase 4
                "intent": result.get("query_analysis"),  # Intent Parser 결과
                "search_queries": result.get("search_queries", []),  # 생성된 검색 쿼리
                "search_results": result.get("search_results", []),  # 검색 결과 (품질 점수 포함)
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
            mode=str(result.get("mode", "simple")),
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
    settings: Settings = Depends(get_settings),
    svc: ThreadService = Depends(get_svc),
):
    """새 스레드 생성 (첫 메시지 포함, SSE 스트리밍).

    POST /api/threads/stream - 새 대화 시작 (스트리밍)
    """
    logger.info(f"[Thread] Create stream: query='{request.query[:50]}...', mode={request.mode}")

    async def generate():
        try:
            # 새 스레드 생성
            thread = await svc.create_thread(metadata=request.context)

            # 스레드 ID 먼저 전송
            yield f"data: {json.dumps({'type': 'thread_id', 'data': thread.thread_id})}\n\n"

            # 사용자 메시지 저장
            await svc.add_message(
                thread.thread_id,
                role="user",
                content=request.query,
            )

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
                settings=settings,
                query=request.query,
                thread_id=thread.thread_id,
                mode=request.mode,
                metadata=request.context,
                enable_retry=True,  # V8.4: 재시도 활성화
                max_retries=3,
                user_id=None,  # TODO: 인증 구현 후 실제 user_id 전달
            ):
                event_type = event.get("type", "")
                event_data = event.get("data")

                # 이벤트 타입에 따른 처리
                if event_type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'data': event_data})}\n\n"
                    if isinstance(event_data, dict) and "mode" in event_data:
                        mode_used = event_data["mode"]
                    # V8.4: 재시도 정보 수집
                    if isinstance(event_data, dict):
                        if "search_retry_count" in event_data:
                            search_retry_count = event_data["search_retry_count"]
                        if "search_quality_score" in event_data:
                            search_quality_score = event_data["search_quality_score"]
                        if "retry_reason" in event_data:
                            retry_reason = event_data["retry_reason"]

                elif event_type == "query_analysis":
                    # 쿼리 재정의 결과 전송 (Perplexity 스타일 UI용)
                    intent = event_data  # 저장용
                    yield f"data: {json.dumps({'type': 'query_analysis', 'data': event_data})}\n\n"

                elif event_type == "token":
                    # 토큰 단위 스트리밍 - 점진적으로 응답 축적
                    token = event_data if isinstance(event_data, str) else str(event_data or "")
                    full_response += token
                    yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"

                elif event_type == "content":
                    # 전체 콘텐츠 업데이트 (non-streaming fallback)
                    full_response = event_data if isinstance(event_data, str) else str(event_data)
                    yield f"data: {json.dumps({'type': 'content', 'data': full_response})}\n\n"

                elif event_type == "sources":
                    sources = event_data if isinstance(event_data, list) else []
                    yield f"data: {json.dumps({'type': 'sources', 'data': sources})}\n\n"

                elif event_type == "citations":  # Phase 4
                    citations = event_data if isinstance(event_data, list) else []
                    yield f"data: {json.dumps({'type': 'citations', 'data': citations})}\n\n"

                elif event_type == "search_queries":  # V8.3
                    search_queries = event_data if isinstance(event_data, list) else []
                    yield f"data: {json.dumps({'type': 'search_queries', 'data': search_queries})}\n\n"

                elif event_type == "search_results":  # V8.3
                    search_results = event_data if isinstance(event_data, list) else []
                    yield f"data: {json.dumps({'type': 'search_results', 'data': search_results})}\n\n"

                elif event_type == "search_retry":  # V8.4: 재시도 이벤트
                    # 재시도 정보 업데이트
                    if isinstance(event_data, dict):
                        search_retry_count = event_data.get("retry_count", search_retry_count)
                        search_quality_score = event_data.get("quality_score", search_quality_score)
                        retry_reason = event_data.get("reason", retry_reason)
                        if "failed_query" in event_data:
                            failed_queries.append(event_data["failed_query"])
                    # 클라이언트에 재시도 정보 전송
                    yield f"data: {json.dumps({'type': 'search_retry', 'data': event_data})}\n\n"

                elif event_type == "done":
                    # AI 응답 저장 (V8.4: 재시도 메타데이터 포함)
                    if full_response:
                        await svc.add_message(
                            thread.thread_id,
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
                                # V8.4: 재시도 정보
                                "search_retry_count": search_retry_count,
                                "search_quality_score": search_quality_score,
                                "failed_queries": failed_queries,
                                "retry_reason": retry_reason,
                            },
                        )
                    yield f"data: {json.dumps({'type': 'done', 'data': None})}\n\n"

                elif event_type == "error":
                    yield f"data: {json.dumps({'type': 'error', 'data': str(event_data)})}\n\n"

        except Exception as e:
            logger.exception(f"[Thread] Create stream error: {e}")
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
):
    """스레드 목록 조회.

    GET /api/threads
    """
    threads = await svc.list_threads(limit=limit, offset=offset)
    return ThreadListResponse(
        threads=threads,
        total=len(threads),
    )


@router.get("/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread(
    thread_id: str,
    svc: ThreadService = Depends(get_svc),
):
    """스레드 상세 조회.

    GET /api/threads/{thread_id}
    """
    thread = await svc.get_thread(thread_id)
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
):
    """스레드 삭제.

    DELETE /api/threads/{thread_id}
    """
    deleted = await svc.delete_thread(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")

    return {"message": "스레드가 삭제되었습니다", "thread_id": thread_id}


@router.post("/{thread_id}/messages", response_model=ThreadResponse)
async def add_message(
    thread_id: str,
    request: AddMessageRequest,
    settings: Settings = Depends(get_settings),
    svc: ThreadService = Depends(get_svc),
):
    """기존 스레드에 메시지 추가 (비스트리밍).

    POST /api/threads/{thread_id}/messages
    """
    logger.info(f"[Thread] Add message: thread_id={thread_id}, query='{request.query[:50]}...', mode={request.mode}")

    # 스레드 존재 확인
    thread = await svc.get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")

    try:
        # 사용자 메시지 저장
        await svc.add_message(
            thread_id,
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
            role="assistant",
            content=result.get("response", ""),
            metadata={
                "mode": str(result.get("mode", "simple")),
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
            mode=str(result.get("mode", "simple")),
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
    settings: Settings = Depends(get_settings),
    svc: ThreadService = Depends(get_svc),
):
    """기존 스레드에 메시지 추가 (SSE 스트리밍).

    POST /api/threads/{thread_id}/messages/stream
    """
    logger.info(f"[Thread] Add message stream: thread_id={thread_id}, query='{request.query[:50]}...', mode={request.mode}")

    # 스레드 존재 확인
    thread = await svc.get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")

    async def generate():
        try:
            # 스레드 ID 전송
            yield f"data: {json.dumps({'type': 'thread_id', 'data': thread_id})}\n\n"

            # 사용자 메시지 저장
            await svc.add_message(
                thread_id,
                role="user",
                content=request.query,
            )

            # AI Agent 스트리밍 실행
            full_response = ""
            mode_used = "simple"
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
                query=request.query,
                thread_id=thread_id,
                mode=request.mode,
                metadata=request.context,
                enable_retry=True,
                max_retries=3,
                user_id=None,
            ):
                event_type = event.get("type", "")
                event_data = event.get("data")

                if event_type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'data': event_data})}\n\n"
                    if isinstance(event_data, dict) and "mode" in event_data:
                        mode_used = event_data["mode"]
                    if isinstance(event_data, dict):
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
                    token = event_data if isinstance(event_data, str) else str(event_data or "")
                    full_response += token
                    yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"

                elif event_type == "content":
                    full_response = event_data if isinstance(event_data, str) else str(event_data)
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
                        search_retry_count = event_data.get("retry_count", search_retry_count)
                        search_quality_score = event_data.get("quality_score", search_quality_score)
                        retry_reason = event_data.get("reason", retry_reason)
                        if "failed_query" in event_data:
                            failed_queries.append(event_data["failed_query"])
                    yield f"data: {json.dumps({'type': 'search_retry', 'data': event_data})}\n\n"

                elif event_type == "done":
                    if full_response:
                        await svc.add_message(
                            thread_id,
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
                            },
                        )
                    yield f"data: {json.dumps({'type': 'done', 'data': None})}\n\n"

                elif event_type == "error":
                    yield f"data: {json.dumps({'type': 'error', 'data': str(event_data)})}\n\n"

        except Exception as e:
            logger.exception(f"[Thread] Add message stream error: {e}")
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
):
    """마지막 AI 답변 재생성 (SSE 스트리밍).

    POST /api/threads/{thread_id}/regenerate

    1. 마지막 assistant 메시지 삭제
    2. 마지막 user 메시지로 AI 재요청
    """
    logger.info(f"[Thread] Regenerate: thread_id={thread_id}, mode={request.mode}")

    # 스레드 존재 확인
    thread = await svc.get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="스레드를 찾을 수 없습니다")

    # 마지막 assistant 메시지 삭제하고 user 쿼리 가져오기
    last_user_query = await svc.remove_last_assistant_message(thread_id)
    if not last_user_query:
        raise HTTPException(
            status_code=400,
            detail="재생성할 답변이 없습니다 (마지막 메시지가 assistant가 아님)"
        )

    async def generate():
        try:
            # AI Agent 스트리밍 실행
            full_response = ""
            mode_used = request.mode or "auto"
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
                user_id=None,
            ):
                event_type = event.get("type", "")
                event_data = event.get("data")

                if event_type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'data': event_data})}\n\n"
                    if isinstance(event_data, dict) and "mode" in event_data:
                        mode_used = event_data["mode"]
                    if isinstance(event_data, dict):
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
                    token = event_data if isinstance(event_data, str) else str(event_data or "")
                    full_response += token
                    yield f"data: {json.dumps({'type': 'token', 'data': token})}\n\n"

                elif event_type == "content":
                    full_response = event_data if isinstance(event_data, str) else str(event_data)
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
                        search_retry_count = event_data.get("retry_count", search_retry_count)
                        search_quality_score = event_data.get("quality_score", search_quality_score)
                        retry_reason = event_data.get("reason", retry_reason)
                        if "failed_query" in event_data:
                            failed_queries.append(event_data["failed_query"])
                    yield f"data: {json.dumps({'type': 'search_retry', 'data': event_data})}\n\n"

                elif event_type == "done":
                    if full_response:
                        await svc.add_message(
                            thread_id,
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
