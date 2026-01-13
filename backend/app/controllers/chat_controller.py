"""채팅 API 엔드포인트.

OpenAI SDK를 사용하여 LiteLLM 프록시를 통해 GPU/NPU로 자동 라우팅됩니다.
"""
import os
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI

from ..core.logging import logger

router = APIRouter(prefix="/api", tags=["chat"])


def get_litellm_base_url() -> str:
    """LiteLLM 프록시 URL 반환."""
    if litellm_url := os.getenv("LITELLM_BASE_URL"):
        return litellm_url
    return "http://localhost:4000"


def get_litellm_api_key() -> str:
    """LiteLLM API 키 반환."""
    return os.getenv("LITELLM_API_KEY", "sk-litellm-master")


def get_litellm_model() -> str:
    """LiteLLM 모델명 반환."""
    return os.getenv("LITELLM_MODEL", "qwen3-4b")


@lru_cache(maxsize=1)
def get_async_openai_client() -> AsyncOpenAI:
    """AsyncOpenAI 클라이언트 싱글톤."""
    base_url = get_litellm_base_url().rstrip("/")
    return AsyncOpenAI(
        base_url=f"{base_url}/v1",
        api_key=get_litellm_api_key(),
        timeout=120.0,
    )


@router.post("/chat")
async def chat_completions(request: Request):
    """채팅 완성 API (SSE 스트리밍 지원).
    
    OpenAI SDK를 통해 LiteLLM 프록시와 통신합니다.
    LiteLLM이 GPU/NPU 중 여유 있는 Provider를 자동 선택합니다.
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
        
        litellm_model = get_litellm_model()
        
        logger.info(f"[Chat] Request to LiteLLM: model={litellm_model}, messages_count={len(openai_messages)}")
        
        async def generate():
            try:
                client = get_async_openai_client()
                
                stream = await client.chat.completions.create(
                    model=litellm_model,
                    messages=openai_messages,
                    stream=True,
                )
                
                async for chunk in stream:
                    # OpenAI 표준 SSE JSON 형식으로 전송
                    chunk_json = chunk.model_dump_json()
                    yield f"data: {chunk_json}\n\n".encode('utf-8')
                
                yield "data: [DONE]\n\n".encode('utf-8')
                        
            except Exception as e:
                logger.exception(f"[Chat] Streaming error: {e}")
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
