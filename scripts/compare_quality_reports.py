#!/usr/bin/env python3
"""Quality test JSON 리포트 비교 스크립트.

사용 예시:
    python scripts/compare_quality_reports.py \
      --baseline quality_test_reports/quality_test_20260213_120000.json \
      --candidate quality_test_reports/quality_test_20260213_153000.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_report(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _avg(values: list[float], default: float = 0.0) -> float:
    if not values:
        return default
    return sum(values) / len(values)


def _extract_search_quality_stats(report: dict[str, Any]) -> dict[str, float]:
    evaluations = report.get("evaluations", [])
    search_evals = [
        e
        for e in evaluations
        if isinstance(e, dict) and e.get("evaluator_name") == "SearchQuality"
    ]
    if not search_evals:
        return {}

    scores = [_safe_float(e.get("score"), 0.0) for e in search_evals]
    metric_dicts = [
        e.get("metrics", {})
        for e in search_evals
        if isinstance(e.get("metrics", {}), dict)
    ]

    avg_quality = _avg(
        [_safe_float(m.get("avg_quality_score"), 0.0) for m in metric_dicts], 0.0
    )
    avg_results = _avg(
        [_safe_float(m.get("avg_result_count"), 0.0) for m in metric_dicts], 0.0
    )
    avg_retry = _avg(
        [_safe_float(m.get("avg_retry_count"), 0.0) for m in metric_dicts], 0.0
    )
    avg_content_coverage = _avg(
        [_safe_float(m.get("avg_content_coverage_ratio"), 0.5) for m in metric_dicts],
        0.5,
    )
    avg_second_stage = _avg(
        [_safe_float(m.get("avg_second_stage_score"), 50.0) for m in metric_dicts],
        50.0,
    )
    enrichment_rate = _avg(
        [_safe_float(m.get("content_enrichment_rate"), 0.0) for m in metric_dicts],
        0.0,
    )

    low_content_coverage_rates = []
    for m in metric_dicts:
        retry_dist = m.get("retry_reason_distribution", {})
        if isinstance(retry_dist, dict):
            low_content_coverage_rates.append(
                _safe_float(retry_dist.get("low_content_coverage"), 0.0)
            )

    return {
        "count": float(len(search_evals)),
        "avg_score": _avg(scores, 0.0),
        "avg_quality_score": avg_quality,
        "avg_result_count": avg_results,
        "avg_retry_count": avg_retry,
        "avg_content_coverage_ratio": avg_content_coverage,
        "avg_second_stage_score": avg_second_stage,
        "content_enrichment_rate": enrichment_rate,
        "low_content_coverage_rate": _avg(low_content_coverage_rates, 0.0),
    }


def _print_delta(label: str, base: float, cand: float, unit: str = "") -> None:
    delta = cand - base
    sign = "+" if delta >= 0 else ""
    print(f"- {label}: {base:.2f}{unit} -> {cand:.2f}{unit} ({sign}{delta:.2f}{unit})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Search quality report comparator")
    parser.add_argument("--baseline", required=True, help="기준 리포트 JSON 경로")
    parser.add_argument("--candidate", required=True, help="비교 대상 리포트 JSON 경로")
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    candidate_path = Path(args.candidate)
    if not baseline_path.exists():
        print(f"[ERROR] baseline 파일이 없습니다: {baseline_path}")
        return 1
    if not candidate_path.exists():
        print(f"[ERROR] candidate 파일이 없습니다: {candidate_path}")
        return 1

    baseline_report = _load_report(str(baseline_path))
    candidate_report = _load_report(str(candidate_path))

    baseline_stats = _extract_search_quality_stats(baseline_report)
    candidate_stats = _extract_search_quality_stats(candidate_report)

    if not baseline_stats or not candidate_stats:
        print("[ERROR] SearchQuality 평가 데이터가 부족합니다.")
        return 1

    print("\nSearchQuality 비교 결과")
    print(f"- baseline:  {baseline_path}")
    print(f"- candidate: {candidate_path}")

    _print_delta("평균 점수", baseline_stats["avg_score"], candidate_stats["avg_score"])
    _print_delta(
        "평균 품질 점수",
        baseline_stats["avg_quality_score"],
        candidate_stats["avg_quality_score"],
    )
    _print_delta(
        "평균 결과 수",
        baseline_stats["avg_result_count"],
        candidate_stats["avg_result_count"],
    )
    _print_delta(
        "평균 재시도 수",
        baseline_stats["avg_retry_count"],
        candidate_stats["avg_retry_count"],
    )
    _print_delta(
        "본문 커버리지",
        baseline_stats["avg_content_coverage_ratio"] * 100,
        candidate_stats["avg_content_coverage_ratio"] * 100,
        "%",
    )
    _print_delta(
        "본문 수집 적용률",
        baseline_stats["content_enrichment_rate"],
        candidate_stats["content_enrichment_rate"],
        "%",
    )
    _print_delta(
        "2차 리랭킹 점수",
        baseline_stats["avg_second_stage_score"],
        candidate_stats["avg_second_stage_score"],
    )
    _print_delta(
        "low_content_coverage 비율",
        baseline_stats["low_content_coverage_rate"],
        candidate_stats["low_content_coverage_rate"],
        "%",
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
