#!/usr/bin/env python3
"""Generate a deterministic SearchQuality report from fixture cases."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.quality_testing.core.interfaces import ConversationData  # noqa: E402
from app.quality_testing.evaluators.search_evaluator import SearchQualityEvaluator  # noqa: E402


def _load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Fixture file must be a list")

    cases: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id", "")).strip()
        metadata = item.get("metadata")
        if not case_id or not isinstance(metadata, dict):
            continue
        cases.append({"id": case_id, "metadata": metadata})

    if not cases:
        raise ValueError("No valid cases found in fixture file")
    return cases


def _build_conversation(case_id: str, metadata: dict[str, Any]) -> ConversationData:
    return ConversationData(
        conversation_id=case_id,
        title=f"fixture-{case_id}",
        created_at=0.0,
        updated_at=0.0,
        metadata={"source": "ci-fixture"},
        messages=[
            {
                "role": "assistant",
                "content": "fixture response",
                "metadata": metadata,
            }
        ],
    )


def _build_summary(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(evaluations)
    passed = sum(1 for result in evaluations if bool(result.get("passed")))
    scores = [float(result.get("score", 0.0)) for result in evaluations]

    avg_score = sum(scores) / len(scores) if scores else 0.0
    pass_rate = (passed / total * 100.0) if total else 0.0

    return {
        "total_evaluations": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": pass_rate,
        "by_evaluator": {
            "SearchQuality": {
                "total": total,
                "passed": passed,
                "avg_score": avg_score,
                "pass_rate": pass_rate,
            }
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate SearchQuality report from deterministic fixtures"
    )
    parser.add_argument(
        "--cases",
        required=True,
        help="Fixture JSON path (.ci/quality/fixtures/search_quality_cases.json)",
    )
    parser.add_argument("--output", required=True, help="Output JSON report path")
    parser.add_argument(
        "--threshold",
        type=float,
        default=60.0,
        help="SearchQuality pass threshold (default: 60.0)",
    )
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.exists():
        print(f"[ERROR] fixture file not found: {cases_path}")
        return 1

    cases = _load_cases(cases_path)
    evaluator = SearchQualityEvaluator(threshold=args.threshold)

    evaluations: list[dict[str, Any]] = []
    for case in cases:
        conversation = _build_conversation(case["id"], case["metadata"])
        result = evaluator.evaluate(conversation)
        evaluations.append(result.model_dump())

    report = {
        "test_run_id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "conversations_tested": len(cases),
        "evaluations": evaluations,
        "summary": _build_summary(evaluations),
        "config": {
            "source": "ci-fixture",
            "fixture_path": str(cases_path),
            "threshold": args.threshold,
        },
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[OK] report generated: {output_path}")
    print(f"[INFO] cases: {len(cases)}")
    print(f"[INFO] pass_rate: {report['summary']['pass_rate']:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
