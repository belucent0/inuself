#!/usr/bin/env python3
"""품질 비교용 AI 스레드 생성 스크립트.

동일 질문셋으로 새 thread를 생성하여 A/B 설정 비교에 사용한다.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib import request, error


DEFAULT_QUERIES = [
    "한국에서 롯데리아는 수제버거 브랜드로 인식되나요? 근거 중심으로 설명해줘.",
    "2026년 기준 AI 에이전트 트렌드 핵심 변화 3가지를 출처와 함께 정리해줘.",
    "FastAPI와 Spring Boot를 API 서버 관점에서 장단점 비교해줘.",
    "RAG 평가에서 precision, recall, MRR 차이를 실제 예시로 설명해줘.",
    "최근 국내 패스트푸드 시장에서 프리미엄 버거 인식 변화가 있었는지 알려줘.",
]


def _load_queries(path: str | None) -> list[str]:
    if not path:
        return DEFAULT_QUERIES

    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"queries file not found: {target}")

    if target.suffix.lower() == ".json":
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON queries file must be a list of strings")
        return [str(x).strip() for x in data if str(x).strip()]

    lines = [line.strip() for line in target.read_text(encoding="utf-8").splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def _percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    index = max(0, min(len(sorted_vals) - 1, int(round((len(sorted_vals) - 1) * 0.95))))
    return sorted_vals[index]


def _create_thread(
    base_url: str, query: str, mode: str, timeout: float
) -> tuple[dict[str, Any], float]:
    url = f"{base_url.rstrip('/')}/api/threads"
    payload = json.dumps({"query": query, "mode": mode}).encode("utf-8")
    req = request.Request(
        url=url,
        method="POST",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    started = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"request failed: {e}") from e

    elapsed_ms = (time.perf_counter() - started) * 1000
    data = json.loads(body)
    return data, elapsed_ms


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate thread cases for A/B quality tests"
    )
    parser.add_argument("--label", required=True, help="실험 라벨 (예: A, B)")
    parser.add_argument(
        "--base-url", default="http://localhost:8000", help="Backend base URL"
    )
    parser.add_argument("--mode", default="hybrid", help="AI mode (default: hybrid)")
    parser.add_argument(
        "--timeout", type=float, default=180.0, help="요청 타임아웃(초)"
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="질문별 재시도 횟수 (기본 1)",
    )
    parser.add_argument(
        "--queries-file",
        default=None,
        help="질문셋 파일(.txt/.json), 없으면 기본 질문셋 사용",
    )
    parser.add_argument(
        "--max-queries",
        type=int,
        default=None,
        help="실행할 최대 질문 수 (기본 전체)",
    )
    parser.add_argument(
        "--output-dir", default="quality_test_reports", help="결과 저장 루트 디렉토리"
    )
    args = parser.parse_args()

    queries = _load_queries(args.queries_file)
    if args.max_queries is not None:
        queries = queries[: max(0, args.max_queries)]
    if not queries:
        print("[ERROR] queries is empty")
        return 1

    output_dir = Path(args.output_dir) / args.label
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] label={args.label}, mode={args.mode}, queries={len(queries)}")
    print(f"[INFO] backend={args.base_url}")

    thread_ids: list[str] = []
    latencies: list[float] = []
    rows: list[dict[str, Any]] = []
    failures = 0

    for i, q in enumerate(queries, 1):
        print(f"\n[{i}/{len(queries)}] {q[:70]}")
        last_error: Exception | None = None
        max_attempts = max(1, args.retries + 1)

        for attempt in range(1, max_attempts + 1):
            try:
                data, elapsed_ms = _create_thread(
                    base_url=args.base_url,
                    query=q,
                    mode=args.mode,
                    timeout=args.timeout,
                )
                thread_id = str(data.get("thread_id", ""))
                if not thread_id:
                    raise RuntimeError("thread_id missing in response")

                thread_ids.append(thread_id)
                latencies.append(elapsed_ms)
                rows.append(
                    {
                        "query": q,
                        "thread_id": thread_id,
                        "mode": data.get("mode"),
                        "latency_ms": round(elapsed_ms, 2),
                        "attempt": attempt,
                        "sources_count": len(data.get("sources", []) or []),
                        "citations_count": len(data.get("citations", []) or []),
                        "response_length": len(str(data.get("response", ""))),
                    }
                )
                print(
                    f"  -> thread={thread_id[:8]}..., latency={elapsed_ms:.0f}ms, "
                    f"sources={rows[-1]['sources_count']}, citations={rows[-1]['citations_count']}, "
                    f"attempt={attempt}"
                )
                last_error = None
                break
            except Exception as e:
                last_error = e
                if attempt < max_attempts:
                    print(f"  -> [WARN] attempt {attempt} failed: {e}")
                    time.sleep(1.5)

        if last_error is not None:
            failures += 1
            rows.append({"query": q, "error": str(last_error)})
            print(f"  -> [ERROR] {last_error}")

    summary = {
        "label": args.label,
        "base_url": args.base_url,
        "mode": args.mode,
        "generated_at": time.time(),
        "query_count": len(queries),
        "success_count": len(thread_ids),
        "failure_count": failures,
        "thread_ids": thread_ids,
        "latency": {
            "avg_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "p95_ms": round(_percentile_95(latencies), 2) if latencies else 0.0,
        },
        "results": rows,
    }

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    summary_path = output_dir / f"generated_threads_{timestamp}.json"
    ids_path = output_dir / f"generated_thread_ids_{timestamp}.txt"

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ids_path.write_text("\n".join(thread_ids), encoding="utf-8")

    print("\n[SUMMARY]")
    print(f"- success: {len(thread_ids)}/{len(queries)}")
    print(f"- avg latency: {summary['latency']['avg_ms']:.0f}ms")
    print(f"- p95 latency: {summary['latency']['p95_ms']:.0f}ms")
    print(f"- summary file: {summary_path}")
    print(f"- thread ids file: {ids_path}")

    return 0 if len(thread_ids) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
