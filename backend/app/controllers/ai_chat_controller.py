"""AI 채팅 API 엔드포인트 (LangGraph 기반).

V8.0: LangGraph 워크플로우를 사용한 AI 모드 채팅 API.
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
from ..services.conversation_service import (
    get_conversation_service,
    ConversationService,
)

router = APIRouter(prefix="/api/ai", tags=["ai-chat"])


class ChatRequest(BaseModel):
    """AI 채팅 요청."""
    query: str = Field(..., description="사용자 질문", min_length=1)
    mode: str = Field(default="auto", description="AI 모드 (auto, simple, search, rag, reasoning, hybrid)")
    conversation_id: str | None = Field(default=None, description="대화 ID (새 대화는 null)")
    context: dict | None = Field(default=None, description="추가 컨텍스트 (RAG용 content_ids 등)")


class ChatResponse(BaseModel):
    """AI 채팅 응답."""
    response: str = Field(..., description="AI 응답")
    conversation_id: str = Field(..., description="대화 ID")
    mode: str = Field(..., description="사용된 AI 모드")
    sources: list[dict] = Field(default=[], description="참조 소스 목록")
    thinking_steps: list[dict] = Field(default=[], description="사고 과정")


class ConversationListResponse(BaseModel):
    """대화 목록 응답."""
    conversations: list[dict] = Field(..., description="대화 목록")
    total: int = Field(..., description="전체 개수")


class ConversationDetailResponse(BaseModel):
    """대화 상세 응답."""
    conversation_id: str
    title: str
    messages: list[dict]
    created_at: float
    updated_at: float


def get_conv_service() -> ConversationService:
    """대화 서비스 의존성."""
    return get_conversation_service()


@router.post("/chat", response_model=ChatResponse)
async def ai_chat(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),
    conv_service: ConversationService = Depends(get_conv_service),
):
    """AI 채팅 API (비스트리밍).

    LangGraph 워크플로우를 실행하여 응답을 생성합니다.
    """
    logger.info(f"[AI Chat] Request: query='{request.query[:50]}...', mode={request.mode}")

    try:
        # 대화 조회 또는 생성
        conversation = await conv_service.get_or_create_conversation(
            request.conversation_id,
            metadata=request.context,
        )

        # 사용자 메시지 저장
        await conv_service.add_message(
            conversation.conversation_id,
            role="user",
            content=request.query,
        )

        # AI Agent 실행
        result = await run_ai_agent(
            settings=settings,
            query=request.query,
            conversation_id=conversation.conversation_id,
            mode=request.mode,
            metadata=request.context,
        )

        # AI 응답 저장
        await conv_service.add_message(
            conversation.conversation_id,
            role="assistant",
            content=result.get("response", ""),
            metadata={
                "mode": str(result.get("mode", "simple")),
                "sources": result.get("sources", []),
            },
        )

        return ChatResponse(
            response=result.get("response", ""),
            conversation_id=conversation.conversation_id,
            mode=str(result.get("mode", "simple")),
            sources=result.get("sources", []),
            thinking_steps=result.get("thinking_steps", []),
        )

    except Exception as e:
        logger.exception(f"[AI Chat] Error: {e}")
        raise HTTPException(status_code=500, detail=f"AI 처리 실패: {str(e)}")


@router.post("/chat/stream")
async def ai_chat_stream(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),
    conv_service: ConversationService = Depends(get_conv_service),
):
    """AI 채팅 API (SSE 스트리밍).

    LangGraph 워크플로우를 스트리밍으로 실행합니다.
    """
    logger.info(f"[AI Chat Stream] Request: query='{request.query[:50]}...', mode={request.mode}")

    async def generate():
        try:
            # 대화 조회 또는 생성
            conversation = await conv_service.get_or_create_conversation(
                request.conversation_id,
                metadata=request.context,
            )

            # 대화 ID 먼저 전송
            yield f"data: {json.dumps({'type': 'conversation_id', 'data': conversation.conversation_id})}\n\n"

            # 사용자 메시지 저장
            await conv_service.add_message(
                conversation.conversation_id,
                role="user",
                content=request.query,
            )

            # AI Agent 스트리밍 실행
            full_response = ""
            mode_used = "simple"
            sources = []

            async for event in stream_ai_agent(
                settings=settings,
                query=request.query,
                conversation_id=conversation.conversation_id,
                mode=request.mode,
                metadata=request.context,
                user_id=None,  # TODO: 인증 구현 후 실제 user_id 전달
            ):
                event_type = event.get("type", "")
                event_data = event.get("data")

                # 이벤트 타입에 따른 처리
                if event_type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'data': event_data})}\n\n"
                    if isinstance(event_data, dict) and "mode" in event_data:
                        mode_used = event_data["mode"]

                elif event_type == "query_analysis":
                    # 쿼리 재정의 결과 전송 (Perplexity 스타일 UI용)
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

                elif event_type == "done":
                    # AI 응답 저장
                    if full_response:
                        await conv_service.add_message(
                            conversation.conversation_id,
                            role="assistant",
                            content=full_response,
                            metadata={"mode": mode_used, "sources": sources},
                        )
                    yield f"data: {json.dumps({'type': 'done', 'data': None})}\n\n"

                elif event_type == "error":
                    yield f"data: {json.dumps({'type': 'error', 'data': str(event_data)})}\n\n"

        except Exception as e:
            logger.exception(f"[AI Chat Stream] Error: {e}")
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


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    limit: int = 20,
    offset: int = 0,
    conv_service: ConversationService = Depends(get_conv_service),
):
    """대화 목록 조회."""
    conversations = await conv_service.list_conversations(limit=limit, offset=offset)
    return ConversationListResponse(
        conversations=conversations,
        total=len(conversations),
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    conv_service: ConversationService = Depends(get_conv_service),
):
    """대화 상세 조회."""
    conversation = await conv_service.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")

    return ConversationDetailResponse(
        conversation_id=conversation.conversation_id,
        title=conversation.title,
        messages=[m.to_dict() for m in conversation.messages],
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    conv_service: ConversationService = Depends(get_conv_service),
):
    """대화 삭제."""
    deleted = await conv_service.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")

    return {"message": "대화가 삭제되었습니다", "conversation_id": conversation_id}
