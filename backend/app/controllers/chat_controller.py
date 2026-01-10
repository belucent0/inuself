"""채팅 API 엔드포인트."""
import os
import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from ..core.logging import logger

router = APIRouter(prefix="/api", tags=["chat"])


def get_flm_base_url() -> str:
    """FastFlowLM 서버 URL 반환."""
    if flm_base_url := os.getenv("FLM_BASE_URL"):
        return flm_base_url
    
    # 기본값: Docker 환경에서는 host.docker.internal, 로컬에서는 127.0.0.1
    default_host = "host.docker.internal"
    flm_port = os.getenv("FLM_PORT", "11434")
    return f"http://{default_host}:{flm_port}"


@router.post("/chat")
async def chat_completions(request: Request):
    """채팅 완성 API (SSE 스트리밍 지원).
    
    FastFlowLM 서버의 OpenAI 호환 API를 프록시하여 SSE(Server-Sent Events) 형식으로
    스트리밍 응답을 클라이언트에 전달합니다.
    
    - FastFlowLM 서버가 `stream: True`일 때 SSE 형식으로 응답
    - 백엔드는 받은 청크를 그대로 전달 (변환 없음)
    - 클라이언트는 `text/event-stream` 형식으로 파싱
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
        
        flm_base_url = os.getenv("FLM_BASE_URL") or get_flm_base_url()
        flm_model = os.getenv("FLM_LLM_MODEL", "qwen3-it:4b")
        
        logger.info(f"[Chat] Request to FastFlowLM: model={flm_model}, messages_count={len(openai_messages)}")
        
        async def generate():
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                    async with client.stream(
                        "POST",
                        f"{flm_base_url}/v1/chat/completions",
                        json={
                            "model": flm_model,
                            "messages": openai_messages,
                            "stream": True,
                        },
                    ) as response:
                        if response.status_code != 200:
                            error_text = await response.aread()
                            logger.error(f"[Chat] FastFlowLM error: {response.status_code} - {error_text.decode()}")
                            yield f"data: {error_text.decode()}\n\n".encode('utf-8')
                            return
                        
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                yield chunk
                                
            except httpx.TimeoutException:
                logger.error("[Chat] FastFlowLM 서버 연결 타임아웃")
                yield 'data: {"error": "FastFlowLM 서버 연결 타임아웃"}\n\n'.encode('utf-8')
            except Exception as e:
                logger.exception(f"[Chat] Error: {e}")
                yield f'data: {{"error": "{str(e)}"}}\n\n'.encode('utf-8')
        
        # SSE(Server-Sent Events) 형식으로 스트리밍 응답
        # FastFlowLM 서버가 보낸 청크를 그대로 전달 (변환 없음)
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",  # SSE MIME 타입
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",  # 연결 유지
                "X-Accel-Buffering": "no",  # Nginx 버퍼링 방지
            }
        )
        
    except Exception as e:
        logger.exception(f"[Chat] Request processing error: {e}")
        raise HTTPException(status_code=500, detail=f"채팅 요청 처리 실패: {str(e)}")
