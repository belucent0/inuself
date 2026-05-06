"""채팅 API 엔드포인트.

OpenAI SDK를 사용하여 AI Gateway를 통해 추론 컨테이너로 라우팅됩니다.
"""
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..core.ai_gateway import get_async_openai_client
from ..core.logging import logger
from ..core.llm_tier import LLMTier

router = APIRouter(prefix="/api", tags=["chat"])


def get_ai_gateway_model() -> str:
    """현재 채팅에 쓸 tier/모델명 — 환경변수 우선, 기본은 simple tier."""
    return os.getenv("AI_GATEWAY_MODEL", LLMTier.SIMPLE)


@router.post("/chat")
async def chat_completions(request: Request):
    """채팅 완성 API (SSE 스트리밍 지원).
    
    OpenAI SDK를 통해 AI Gateway와 통신합니다.
    AI Gateway가 GPU/NPU 중 여유 있는 Provider를 자동 선택합니다.
    """
    try:
        body = await request.json()
        messages = body.get("messages", [])
        
        if not messages:
            raise HTTPException(status_code=400, detail="messages 필드가 필요합니다")
        
        openai_messages = []
        for msg in messages:
            content = msg.get("content", "")
            if not content and "parts" in msg:
                content = "".join(
                    part.get("text", "")
                    for part in msg["parts"]
                    if part.get("type") == "text"
                )
            
            openai_messages.append({
                "role": msg.get("role", "user"),
                "content": content
            })
        
        ai_gateway_model = get_ai_gateway_model()
        client = get_async_openai_client()

        trace_id = request.headers.get("X-Trace-Id", "no-trace-id")
        logger.info(f"[Chat] Request to AI Gateway: model={ai_gateway_model}, messages_count={len(openai_messages)}, trace_id={trace_id}")

        async def generate():
            try:
                response = await client.chat.completions.create(
                    model=ai_gateway_model,
                    messages=openai_messages,
                    stream=True,
                    extra_body={"metadata": {"trace_id": trace_id}}
                )

                async for chunk in response:
                    chunk_json = chunk.model_dump_json()
                    yield f"data: {chunk_json}\n\n".encode('utf-8')

                yield "data: [DONE]\n\n".encode('utf-8')

            except Exception as e:
                logger.exception(f"[Chat] Error: {e}")
                yield f'data: {{"error": "{str(e)}"}}\n\n'.encode('utf-8')
        
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
        
    except Exception as e:
        logger.exception(f"[Chat] Request processing error: {e}")
        raise HTTPException(status_code=500, detail=f"채팅 요청 처리 실패: {str(e)}")
