"""(legacy) Multimodal OCR smoke test via ai-llm.

ai-llm이 text-only Qwen3-4B-Instruct로 교체된 후 image input은 vLLM에서 거절됩니다.
실제 OCR 검증은 ai-ocr 컨테이너(port 18080, dots.ocr) 또는 ai-gateway /v1/chat/completions
(OCR 라우팅이 ai-ocr으로 자동 분기)를 사용하세요.

Usage:
    python ocr_smoke.py /path/to/image.jpg [prompt]
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import httpx


DEFAULT_PROMPT = (
    "이 이미지에 포함된 모든 텍스트를 한국어로 정확히 추출해줘. "
    "원문 그대로의 줄바꿈과 글머리 기호를 유지하고, 출처 도메인이 있다면 함께 적어줘."
)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: ocr_smoke.py IMAGE [PROMPT]", file=sys.stderr)
        sys.exit(2)

    image_path = Path(sys.argv[1])
    prompt = sys.argv[2] if len(sys.argv) >= 3 else DEFAULT_PROMPT
    base_url = "http://localhost:18000/v1/chat/completions"
    model = "qwen3-4b-instruct"

    img_bytes = image_path.read_bytes()
    b64 = base64.b64encode(img_bytes).decode()
    suffix = image_path.suffix.lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(suffix, "jpeg")
    data_url = f"data:image/{mime};base64,{b64}"

    print(f"[1/3] image  : {image_path} ({len(img_bytes) // 1024} KB, mime=image/{mime})")
    print(f"[2/3] prompt : {prompt}")
    print(f"[3/3] sending request to {base_url}")

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.1,
    }

    t0 = time.perf_counter()
    with httpx.Client(timeout=600) as cli:
        r = cli.post(base_url, json=payload)
    elapsed = time.perf_counter() - t0
    r.raise_for_status()
    j = r.json()

    out = j["choices"][0]["message"]["content"]
    usage = j.get("usage", {})

    print("\n=== EXTRACTED TEXT ===")
    print(out)
    print(f"\n=== USAGE ===")
    print(json.dumps(usage, ensure_ascii=False, indent=2))
    print(f"\n=== wall: {elapsed:.2f}s ===")
    if usage.get("completion_tokens"):
        print(f"=== decode tps: {usage['completion_tokens'] / elapsed:.2f} tok/s (incl. ttft) ===")


if __name__ == "__main__":
    main()
