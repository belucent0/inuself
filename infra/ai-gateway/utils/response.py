"""OpenAI-compatible 응답 포맷 유틸리티."""

import time
import uuid


def build_openai_response(
    content: str | dict,
    model: str,
    usage: dict | None = None,
) -> dict:
    """OpenAI ChatCompletion 응답 포맷으로 변환.

    content가 이미 OpenAI 포맷(choices 키 포함)이면 그대로 반환.
    """
    if isinstance(content, dict) and "choices" in content:
        return content

    if isinstance(content, dict):
        text = content.get("text", content.get("content", str(content)))
    else:
        text = str(content)

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
