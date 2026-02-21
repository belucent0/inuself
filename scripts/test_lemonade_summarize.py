"""tier-summarize → lemonade-server 라우팅 E2E 테스트.

실제 요청 흐름:
  이 스크립트 → Redis stream:chat:requests
    → StreamProcessor → lemonade-server:8084
      → Redis stream:gpu:responses → 이 스크립트

사용:
  python scripts/test_lemonade_summarize.py
  python scripts/test_lemonade_summarize.py --direct   (Redis 없이 HTTP 직접 테스트)
"""

import asyncio
import json
import sys
import time
import uuid
import argparse

# Windows CP949 터미널 UTF-8 강제
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import redis.asyncio as aioredis

REDIS_URL = "redis://localhost:6379/0"
REQUEST_STREAM = "stream:chat:requests"
RESPONSE_STREAM = "stream:gpu:responses"
LEMONADE_URL = "http://localhost:8084/api/v1/chat/completions"

SAMPLE_TRANSCRIPT = """
안녕하세요. 오늘 회의에서는 Q3 매출 결과를 검토하겠습니다.
총 매출은 전분기 대비 12% 증가했으며, 특히 신규 고객 유입이 두드러졌습니다.
다음 분기에는 마케팅 예산을 20% 확대할 예정입니다.
"""


async def test_via_redis():
    """Redis stream을 통한 전체 흐름 테스트."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    request_id = str(uuid.uuid4())

    messages = [
        {"role": "system", "content": "당신은 회의 전사 내용을 요약하는 어시스턴트입니다."},
        {"role": "user", "content": f"다음 회의 내용을 3줄로 요약해주세요:\n{SAMPLE_TRANSCRIPT}"},
    ]

    payload = {
        "request_id": request_id,
        "type": "llm_completion",
        "model": "tier-summarize",
        "messages": json.dumps(messages),
        "max_tokens": "512",
        "temperature": "0.3",
    }

    print(f"[→] Redis stream 요청 전송 (request_id={request_id[:8]}...)")
    print(f"    stream: {REQUEST_STREAM}")
    print(f"    model:  tier-summarize\n")

    await redis.xadd(REQUEST_STREAM, payload)
    sent_at = time.time()

    print("[⏳] 응답 대기 중 (최대 120초)...")
    collected_chunks = []
    final_result = None
    error = None

    deadline = time.time() + 120
    last_chunk_time = time.time()

    while time.time() < deadline:
        messages_raw = await redis.xread(
            {RESPONSE_STREAM: "0-0"}, count=100, block=500
        )
        for _, msgs in (messages_raw or []):
            for msg_id, data in msgs:
                if data.get("request_id") != request_id:
                    continue

                if "error" in data:
                    error = data["error"]
                    break

                if "chunk" in data:
                    chunk = data["chunk"]
                    collected_chunks.append(chunk)
                    print(chunk, end="", flush=True)
                    last_chunk_time = time.time()

                if "result" in data:
                    try:
                        final_result = json.loads(data["result"])
                    except Exception:
                        final_result = data["result"]

        if error:
            break
        if final_result:
            break
        # 마지막 청크 후 3초 지나면 완료로 간주
        if collected_chunks and time.time() - last_chunk_time > 3:
            break

    elapsed = time.time() - sent_at
    await redis.aclose()

    print(f"\n\n{'='*60}")
    if error:
        print(f"[✗] 에러: {error}")
        return False

    if collected_chunks:
        full_text = "".join(collected_chunks)
        print(f"[✓] 스트리밍 응답 수신 완료")
        print(f"    청크 수: {len(collected_chunks)}")
        print(f"    총 길이: {len(full_text)} chars")
        print(f"    소요시간: {elapsed:.1f}초")
        return True

    if final_result:
        print(f"[✓] 응답 수신 (non-streaming)")
        print(f"    소요시간: {elapsed:.1f}초")
        return True

    print(f"[✗] 타임아웃 - 응답 없음 ({elapsed:.1f}초)")
    print("    StreamProcessor가 실행 중인지 확인하세요.")
    return False


async def test_direct_http():
    """lemonade-server HTTP API 직접 테스트 (Redis/StreamProcessor 불필요)."""
    import httpx

    messages = [
        {"role": "system", "content": "당신은 회의 전사 내용을 요약하는 어시스턴트입니다."},
        {"role": "user", "content": f"다음 회의 내용을 3줄로 요약해주세요:\n{SAMPLE_TRANSCRIPT}"},
    ]

    model = "gpt-oss-20b-mxfp4-GGUF"
    print(f"[→] HTTP 직접 요청: {LEMONADE_URL}")
    print(f"    model: {model}\n")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                LEMONADE_URL,
                json={"model": model, "messages": messages, "max_tokens": 512, "temperature": 0.3, "stream": True},
            ) as resp:
                resp.raise_for_status()
                chunks = 0
                total_chars = 0
                sent_at = time.time()

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk_json = json.loads(data_str)
                        content = chunk_json.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                        if content:
                            print(content, end="", flush=True)
                            chunks += 1
                            total_chars += len(content)
                    except json.JSONDecodeError:
                        continue

                elapsed = time.time() - sent_at
                print(f"\n\n{'='*60}")
                print(f"[✓] 완료: {chunks}청크, {total_chars}chars, {elapsed:.1f}초")
                return True

    except Exception as e:
        print(f"\n[✗] 에러: {e}")
        return False


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct", action="store_true", help="Redis 없이 HTTP 직접 테스트")
    args = parser.parse_args()

    print("=" * 60)
    if args.direct:
        print("  lemonade-server 직접 HTTP 테스트")
    else:
        print("  tier-summarize → lemonade-server E2E 테스트 (Redis)")
    print("=" * 60 + "\n")

    if args.direct:
        ok = await test_direct_http()
    else:
        ok = await test_via_redis()

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
