"""채팅 API 엔드포인트.

LiteLLM 프록시를 통해 GPU/NPU로 자동 라우팅됩니다.
"""
import os
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from ..core.logging import logger

router = APIRouter(prefix="/api", tags=["chat"])


def get_litellm_base_url() -> str:
    """LiteLLM 프록시 URL 반환."""
    # 환경변수 우선
    if litellm_url := os.getenv("LITELLM_BASE_URL"):
        return litellm_url
    
    # 개발 환경 (호스트에서 실행): localhost:4000
    # Docker 환경: asr-litellm:4000
    return "http://localhost:4000"


def get_litellm_api_key() -> str:
    """LiteLLM API 키 반환."""
    return os.getenv("LITELLM_API_KEY", "sk-litellm-master")


def get_litellm_model() -> str:
    """LiteLLM 모델명 반환."""
    return os.getenv("LITELLM_MODEL", "qwen3-4b")


@router.post("/chat")
async def chat_completions(request: Request):
    """채팅 완성 API (SSE 스트리밍 지원).
    
    LiteLLM 프록시를 통해 GPU/NPU로 자동 라우팅됩니다.
    LiteLLM이 여유 있는 Provider(GPU 또는 NPU)를 선택합니다.
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
        
        litellm_base_url = get_litellm_base_url()
        litellm_model = get_litellm_model()
        litellm_api_key = get_litellm_api_key()
        
        logger.info(f"[Chat] Request to LiteLLM: url={litellm_base_url}, model={litellm_model}, messages_count={len(openai_messages)}")
        
        async def generate():
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                    async with client.stream(
                        "POST",
                        f"{litellm_base_url}/v1/chat/completions",
                        json={
                            "model": litellm_model,
                            "messages": openai_messages,
                            "stream": True,
                        },
                        headers={
                            "Authorization": f"Bearer {litellm_api_key}",
                            "Content-Type": "application/json",
                        },
                    ) as response:
                        if response.status_code != 200:
                            error_text = await response.aread()
                            logger.error(f"[Chat] LiteLLM error: {response.status_code} - {error_text.decode()}")
                            yield f"data: {error_text.decode()}\n\n".encode('utf-8')
                            return
                        
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                yield chunk
                                
            except httpx.TimeoutException:
                logger.error("[Chat] LiteLLM 서버 연결 타임아웃")
                yield 'data: {"error": "LiteLLM 서버 연결 타임아웃"}\n\n'.encode('utf-8')
            except Exception as e:
                logger.exception(f"[Chat] Error: {e}")
                yield f'data: {{"error": "{str(e)}"}}\n\n'.encode('utf-8')
        
        # SSE(Server-Sent Events) 형식으로 스트리밍 응답
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

