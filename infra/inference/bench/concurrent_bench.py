"""Concurrent load benchmark for vLLM OpenAI-compatible server.

Measures throughput-per-GPU vs. concurrency level — the key vLLM
PagedAttention + Continuous Batching demonstration metric.

Usage:
    python concurrent_bench.py \
        --base-url http://localhost:18000 \
        --model gemma-4-26B-A4B-it-Q4_K_M \
        --concurrency 1,3,5 \
        --requests-per-level 10 \
        --max-tokens 256
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass

import httpx

PROMPTS = [
    "Explain in 3 sentences why mixture-of-experts models can be efficient.",
    "Write a short Python function that reverses a linked list.",
    "Summarize the trade-offs between quantization formats Q4 vs Q8 for LLMs.",
    "List five key differences between vLLM and llama.cpp.",
    "Describe the role of KV cache in transformer inference.",
    "Compare ROCm and CUDA for deep learning workloads in 4 sentences.",
    "What is PagedAttention and why does it matter for serving LLMs?",
    "Explain Per-Layer Embeddings as used in Gemma's E variants.",
    "Give a minimal example of an HTTP server in Go.",
    "Describe how WSL2 exposes GPU devices to Linux containers.",
]


@dataclass
class CallResult:
    success: bool
    ttft_s: float
    total_s: float
    completion_tokens: int
    error: str | None = None


async def one_call(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    prompt: str,
    max_tokens: int,
) -> CallResult:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": True,
    }
    t0 = time.perf_counter()
    ttft = None
    completion_tokens = 0
    try:
        async with client.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            json=payload,
            timeout=httpx.Timeout(300.0, connect=10.0),
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                return CallResult(False, 0, 0, 0, f"HTTP {resp.status_code}: {body[:200]!r}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = chunk["choices"][0].get("delta", {})
                if delta.get("content"):
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    completion_tokens += 1
        total = time.perf_counter() - t0
        return CallResult(True, ttft or total, total, completion_tokens)
    except Exception as e:
        return CallResult(False, 0, time.perf_counter() - t0, 0, repr(e))


async def run_level(
    base_url: str,
    model: str,
    concurrency: int,
    n_requests: int,
    max_tokens: int,
) -> list[CallResult]:
    sem = asyncio.Semaphore(concurrency)
    results: list[CallResult] = []
    async with httpx.AsyncClient() as client:
        async def worker(i: int) -> None:
            async with sem:
                prompt = PROMPTS[i % len(PROMPTS)]
                r = await one_call(client, base_url, model, prompt, max_tokens)
                results.append(r)
                status = "OK" if r.success else f"ERR {r.error}"
                print(
                    f"  [{i+1:>3}/{n_requests}] {status} "
                    f"ttft={r.ttft_s:.2f}s total={r.total_s:.2f}s tokens={r.completion_tokens}"
                )

        tasks = [asyncio.create_task(worker(i)) for i in range(n_requests)]
        await asyncio.gather(*tasks)
    return results


def summarize(level: int, results: list[CallResult], wall_s: float) -> dict:
    ok = [r for r in results if r.success]
    fail = len(results) - len(ok)
    if not ok:
        return {"concurrency": level, "ok": 0, "fail": fail}
    total_tokens = sum(r.completion_tokens for r in ok)
    avg_ttft = sum(r.ttft_s for r in ok) / len(ok)
    avg_total = sum(r.total_s for r in ok) / len(ok)
    per_user_tps = sum(r.completion_tokens / r.total_s for r in ok if r.total_s > 0) / len(ok)
    aggregate_tps = total_tokens / wall_s
    return {
        "concurrency": level,
        "ok": len(ok),
        "fail": fail,
        "total_tokens": total_tokens,
        "wall_s": round(wall_s, 2),
        "avg_ttft_s": round(avg_ttft, 3),
        "avg_total_s": round(avg_total, 3),
        "per_user_tps": round(per_user_tps, 2),
        "aggregate_tps": round(aggregate_tps, 2),
    }


async def amain() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:18000")
    ap.add_argument("--model", required=True)
    ap.add_argument("--concurrency", default="1,3,5")
    ap.add_argument("--requests-per-level", type=int, default=10)
    ap.add_argument("--max-tokens", type=int, default=256)
    args = ap.parse_args()

    levels = [int(x) for x in args.concurrency.split(",")]
    summary: list[dict] = []

    for lvl in levels:
        print(f"\n=== concurrency={lvl} ({args.requests_per_level} requests) ===")
        t0 = time.perf_counter()
        results = await run_level(
            args.base_url, args.model, lvl, args.requests_per_level, args.max_tokens
        )
        wall = time.perf_counter() - t0
        s = summarize(lvl, results, wall)
        summary.append(s)
        print(f"  >> {s}")

    print("\n=== SUMMARY ===")
    print(f"{'conc':>5} {'ok/fail':>10} {'agg_tps':>10} {'per_user':>10} {'avg_ttft':>10}")
    for s in summary:
        ok_fail = f"{s['ok']}/{s['fail']}"
        print(
            f"{s['concurrency']:>5} {ok_fail:>10} "
            f"{s.get('aggregate_tps', 0):>10} {s.get('per_user_tps', 0):>10} "
            f"{s.get('avg_ttft_s', 0):>10}"
        )


if __name__ == "__main__":
    asyncio.run(amain())
