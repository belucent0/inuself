"""채팅 API 엔드포인트.

OpenAI SDK를 사용하여 LiteLLM 프록시를 통해 GPU/NPU로 자동 라우팅됩니다.
"""
import os
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI

from ..core.logging import logger
from ..core.llm_tier import LLMTier

router = APIRouter(prefix="/api", tags=["chat"])


def get_litellm_base_url() -> str:
    """AI Gateway URL 반환."""
    return os.getenv("AI_GATEWAY_URL", "http://ai-gateway:4000")


def get_litellm_api_key() -> str:
    """API 키 반환."""
    return os.getenv("AI_GATEWAY_API_KEY", "")


def get_litellm_model() -> str:
    """모델명 반환."""
    # 티어 기반 라우팅 사용
    return os.getenv("AI_GATEWAY_MODEL", LLMTier.SIMPLE)


@lru_cache(maxsize=1)
def get_async_openai_client() -> AsyncOpenAI:
    """AsyncOpenAI 클라이언트 싱글톤 (LiteLLM 프록시 연결)."""
    base_url = get_litellm_base_url().rstrip("/")
    return AsyncOpenAI(
        base_url=f"{base_url}", # LiteLLM은 /v1 불필요할 수 있으나 보통 포함됨, 하지만 asr-litellm:4000은 보통 root
        # OpenAI SDK requires base_url to effectively be base endpoint. LiteLLM proxy usually exposes /v1 or /chat/completions directly.
        # But here we assume http://asr-litellm:4000
        # Safe to append /v1 if LiteLLM mimics OpenAI exactly.
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
        client = get_async_openai_client()
        
        logger.info(f"[Chat] Request to LiteLLM: model={litellm_model}, messages_count={len(openai_messages)}")
        
        # 온디맨드 서버 시작 요청 (FLM)
        from .websocket_controller import _send_provider_control
        await _send_provider_control("start", "flm")
        
        async def generate():
            try:
                # 1. FLM 서버 직접 헬스체크 (host.docker.internal)
                # LiteLLM에게 요청하기 전에, 실제 Provider가 떴는지 확인
                import httpx
                import asyncio
                import time
                
                # FLM Check URL
                # 참고: Docker 내부에서 Host의 FLM(11434) 접근
                flm_health_url = "http://host.docker.internal:11434/v1/models"
                
                start_time = time.time()
                server_ready = False
                
                async with httpx.AsyncClient(timeout=1.0) as health_client:
                    while time.time() - start_time < 60:
                        try:
                            resp = await health_client.get(flm_health_url)
                            if resp.status_code == 200:
                                server_ready = True
                                logger.info("[Chat] FLM Provider is ready.")
                                break
                        except Exception:
                            # Connection refused or timeout -> server staring
                            pass
                        await asyncio.sleep(0.5)
                
                if not server_ready:
                     yield f'data: {{"error": "AI Server start timeout (60s)"}}\n\n'.encode('utf-8')
                     return

                # 2. LiteLLM으로 실제 추론 요청 (스트리밍 활성화)
                # 사용자의 피드백에 따라 스트리밍 모드로 복구
                # 2. LiteLLM으로 실제 추론 요청 (스트리밍 활성화)
                # ClientTraceId 전파
                trace_id = request.headers.get("X-Trace-Id", "no-trace-id")
                logger.info(f"[Chat] Sending request to LiteLLM: model={litellm_model}, stream=True, trace_id={trace_id}")
                
                response = await client.chat.completions.create(
                    model=litellm_model,
                    messages=openai_messages,
                    stream=True,
                    extra_body={"metadata": {"trace_id": trace_id}}
                )
                
                # 스트리밍 응답을 실시간으로 중계
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
