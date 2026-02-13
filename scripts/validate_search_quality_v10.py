#!/usr/bin/env python3
"""SearchQualityEvaluator V10 메트릭 스모크 테스트."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.quality_testing.core.interfaces import ConversationData
from app.quality_testing.evaluators.search_evaluator import SearchQualityEvaluator


def _build_conversation(metadata: dict) -> ConversationData:
    return ConversationData(
        conversation_id="quality-v10",
        title="quality v10 smoke",
        created_at=0.0,
        updated_at=0.0,
        messages=[
            {
                "role": "assistant",
                "content": "테스트 응답",
                "metadata": metadata,
            }
        ],
    )


def main() -> int:
    evaluator = SearchQualityEvaluator(threshold=60.0)

    good_conversation = _build_conversation(
        {
            "search_queries": ["python docs"],
            "search_retry_count": 0,
            "search_results": [
                {
                    "quality_score": 88.0,
                    "second_stage_score": 84.0,
                    "fetched_content_length": 1600,
                    "content_fetch_quality": 80.0,
                    "content_preview": "Python docs...",
                },
                {
                    "quality_score": 82.0,
                    "second_stage_score": 78.0,
                    "fetched_content_length": 1400,
                    "content_fetch_quality": 75.0,
                    "content_preview": "FastAPI docs...",
                },
                {
                    "quality_score": 80.0,
                    "second_stage_score": 74.0,
                    "fetched_content_length": 1300,
                    "content_fetch_quality": 72.0,
                    "content_preview": "PyTorch docs...",
                },
            ],
        }
    )
    good_result = evaluator.evaluate(good_conversation)
    print(f"[GOOD] score={good_result.score:.1f}, passed={good_result.passed}")
    assert good_result.passed is True
    assert good_result.metrics["avg_content_coverage_ratio"] >= 0.9

    weak_conversation = _build_conversation(
        {
            "search_queries": ["ai trend"],
            "search_retry_count": 2,
            "retry_reason": "low_content_coverage",
            "search_results": [
                {
                    "quality_score": 55.0,
                    "second_stage_score": 40.0,
                    "fetched_content_length": 100,
                    "content_fetch_quality": 12.0,
                    "content_preview": "short",
                },
                {
                    "quality_score": 52.0,
                    "second_stage_score": 38.0,
                    "fetched_content_length": 120,
                    "content_fetch_quality": 15.0,
                    "content_preview": "short",
                },
                {
                    "quality_score": 51.0,
                    "second_stage_score": 36.0,
                    "fetched_content_length": 90,
                    "content_fetch_quality": 10.0,
                    "content_preview": "short",
                },
            ],
        }
    )
    weak_result = evaluator.evaluate(weak_conversation)
    print(f"[WEAK] score={weak_result.score:.1f}, passed={weak_result.passed}")
    assert (
        weak_result.metrics["retry_reason_distribution"]["low_content_coverage"] > 0.0
    )

    print("[OK] SearchQualityEvaluator V10 smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
