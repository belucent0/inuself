#!/usr/bin/env python3
"""API에서 thread를 읽어 품질 평가를 수행한다.

Redis 캐시 유무와 무관하게 DB에 저장된 최신 thread를 평가할 때 사용.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib import request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.quality_testing.core.interfaces import ConversationData
from app.quality_testing.evaluators.intent_evaluator import IntentEvaluator
from app.quality_testing.evaluators.search_evaluator import SearchQualityEvaluator
from app.quality_testing.evaluators.citation_evaluator import CitationEvaluator
from app.quality_testing.evaluators.quality_evaluator import QualityScoreEvaluator


def _load_ids(ids: list[str], ids_file: str | None) -> list[str]:
    all_ids = [x.strip() for x in ids if x.strip()]
    if ids_file:
        p = Path(ids_file)
        if not p.exists():
            raise FileNotFoundError(f"ids file not found: {ids_file}")
        lines = [line.strip() for line in p.read_text(encoding="utf-8").splitlines()]
        all_ids.extend([line for line in lines if line])

    dedup = []
    seen = set()
    for tid in all_ids:
        if tid not in seen:
            seen.add(tid)
            dedup.append(tid)
    return dedup


def _fetch_thread(base_url: str, thread_id: str) -> dict:
    url = f"{base_url.rstrip('/')}/api/threads/{thread_id}"
    with request.urlopen(url, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate threads via API")
    parser.add_argument(
        "--base-url", default="http://localhost:8000", help="Backend base URL"
    )
    parser.add_argument(
        "--thread-id",
        action="append",
        default=[],
        help="평가할 thread id (여러 번 사용 가능)",
    )
    parser.add_argument("--ids-file", default=None, help="thread id 목록 파일(1줄 1개)")
    parser.add_argument("--output", default=None, help="JSON 출력 경로")
    args = parser.parse_args()

    thread_ids = _load_ids(args.thread_id, args.ids_file)
    if not thread_ids:
        print("[ERROR] no thread ids provided")
        return 1

    evaluators = [
        IntentEvaluator(threshold=70.0),
        SearchQualityEvaluator(threshold=60.0),
        CitationEvaluator(threshold=80.0),
        QualityScoreEvaluator(threshold=70.0),
    ]

    rows = []
    for tid in thread_ids:
        thread = _fetch_thread(args.base_url, tid)
        conv = ConversationData(
            conversation_id=tid,
            title=thread.get("title", ""),
            messages=thread.get("messages", []),
            created_at=float(thread.get("created_at", 0.0) or 0.0),
            updated_at=float(thread.get("updated_at", 0.0) or 0.0),
            metadata={},
        )

        eval_result = {}
        for ev in evaluators:
            result = ev.evaluate(conv)
            eval_result[ev.name] = {
                "score": round(result.score, 2),
                "passed": result.passed,
                "metrics": result.metrics,
                "issues": result.issues,
            }

        rows.append({"thread_id": tid, "evaluations": eval_result})

    summary = {
        "generated_at": time.time(),
        "thread_count": len(rows),
        "threads": rows,
    }

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[OK] saved: {out}")

    for row in rows:
        tid = row["thread_id"]
        ev = row["evaluations"]
        print(
            f"{tid} | Intent={ev['Intent']['score']:.1f} "
            f"SearchQuality={ev['SearchQuality']['score']:.1f} "
            f"Citation={ev['Citation']['score']:.1f} "
            f"QualityScore={ev['QualityScore']['score']:.1f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
