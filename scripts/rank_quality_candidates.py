#!/usr/bin/env python3
"""Search 품질 리포트 후보군 랭킹 스크립트.

사용 예시:
    python scripts/rank_quality_candidates.py \
      --report A=quality_test_reports/quality_test_A.json \
      --report B=quality_test_reports/quality_test_B.json \
      --latency A=1450 \
      --latency B=1820
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _avg(values: list[float], default: float = 0.0) -> float:
    if not values:
        return default
    return sum(values) / len(values)


def _parse_kv_pairs(pairs: list[str], *, value_type: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"'{pair}'는 label=value 형식이어야 합니다")
        key, value = pair.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"'{pair}'의 label이 비어 있습니다")

        if value_type == "path":
            parsed[key] = value
        elif value_type == "float":
            parsed[key] = _safe_float(value, default=float("nan"))
        else:
            raise ValueError(f"지원하지 않는 value_type: {value_type}")
    return parsed


def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_search_metrics(report: dict[str, Any]) -> dict[str, float]:
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
        [_safe_float(m.get("avg_quality_score"), 0.0) for m in metric_dicts]
    )
    avg_results = _avg(
        [_safe_float(m.get("avg_result_count"), 0.0) for m in metric_dicts]
    )
    avg_retry = _avg([_safe_float(m.get("avg_retry_count"), 0.0) for m in metric_dicts])
    avg_content_coverage = _avg(
        [_safe_float(m.get("avg_content_coverage_ratio"), 0.5) for m in metric_dicts],
        default=0.5,
    )
    avg_second_stage = _avg(
        [_safe_float(m.get("avg_second_stage_score"), 50.0) for m in metric_dicts],
        default=50.0,
    )
    enrichment_rate = _avg(
        [_safe_float(m.get("content_enrichment_rate"), 0.0) for m in metric_dicts]
    )

    return {
        "avg_score": _avg(scores),
        "avg_quality_score": avg_quality,
        "avg_result_count": avg_results,
        "avg_retry_count": avg_retry,
        "avg_content_coverage_ratio": avg_content_coverage,
        "avg_second_stage_score": avg_second_stage,
        "content_enrichment_rate": enrichment_rate,
    }


def _quality_index(metrics: dict[str, float]) -> float:
    # 품질 우선 통합 인덱스 (0~100)
    return (
        metrics["avg_score"] * 0.55
        + metrics["avg_content_coverage_ratio"] * 100 * 0.20
        + min(max(metrics["avg_second_stage_score"], 0.0), 100.0) * 0.15
        + max(0.0, 100.0 - metrics["avg_retry_count"] * 20.0) * 0.10
    )


def _latency_scores(latency_map: dict[str, float]) -> dict[str, float]:
    if not latency_map:
        return {}

    finite_values = [v for v in latency_map.values() if v == v and v > 0]  # NaN 제외
    if not finite_values:
        return {}

    best = min(finite_values)
    worst = max(finite_values)
    if worst == best:
        return {k: 100.0 for k in latency_map}

    scores: dict[str, float] = {}
    for label, ms in latency_map.items():
        if not (ms == ms and ms > 0):  # NaN 또는 음수
            scores[label] = 0.0
            continue
        normalized = (worst - ms) / (worst - best)
        scores[label] = max(0.0, min(100.0, normalized * 100.0))
    return scores


def main() -> int:
    parser = argparse.ArgumentParser(description="SearchQuality 후보 랭킹")
    parser.add_argument(
        "--report",
        action="append",
        required=True,
        help="label=report_path 형식 (2개 이상)",
    )
    parser.add_argument(
        "--latency",
        action="append",
        default=[],
        help="label=p95_ms 형식 (선택)",
    )
    parser.add_argument(
        "--quality-weight",
        type=float,
        default=0.75,
        help="최종 점수에서 품질 가중치 (기본 0.75)",
    )
    parser.add_argument(
        "--latency-weight",
        type=float,
        default=0.25,
        help="최종 점수에서 지연 가중치 (기본 0.25)",
    )
    args = parser.parse_args()

    report_map = _parse_kv_pairs(args.report, value_type="path")
    if len(report_map) < 2:
        print("[ERROR] --report는 최소 2개 이상 필요합니다")
        return 1

    latency_map = (
        _parse_kv_pairs(args.latency, value_type="float") if args.latency else {}
    )
    latency_score_map = _latency_scores(latency_map)

    rows = []
    for label, path in report_map.items():
        report_path = Path(path)
        if not report_path.exists():
            print(f"[ERROR] report 파일이 없습니다: {report_path}")
            return 1

        report = _load_json(str(report_path))
        metrics = _extract_search_metrics(report)
        if not metrics:
            print(f"[ERROR] SearchQuality 데이터가 없습니다: {report_path}")
            return 1

        quality_idx = _quality_index(metrics)
        latency_idx = latency_score_map.get(label)

        if latency_score_map:
            final_score = (
                quality_idx * args.quality_weight
                + (latency_idx if latency_idx is not None else 0.0)
                * args.latency_weight
            )
        else:
            final_score = quality_idx

        rows.append(
            {
                "label": label,
                "path": str(report_path),
                "final_score": final_score,
                "quality_idx": quality_idx,
                "latency_idx": latency_idx,
                "avg_score": metrics["avg_score"],
                "avg_retry": metrics["avg_retry_count"],
                "coverage": metrics["avg_content_coverage_ratio"] * 100,
                "second_stage": metrics["avg_second_stage_score"],
                "enrichment": metrics["content_enrichment_rate"],
                "latency_ms": latency_map.get(label),
            }
        )

    rows.sort(key=lambda x: x["final_score"], reverse=True)

    print("\nSearchQuality Candidate Ranking")
    print(
        "label | final | quality | latency_idx | avg_score | retry | coverage% | second_stage | enrich% | p95_ms"
    )
    print("-" * 112)
    for row in rows:
        latency_idx_text = (
            f"{row['latency_idx']:.1f}" if row["latency_idx"] is not None else "-"
        )
        latency_ms_text = f"{row['latency_ms']:.0f}" if row["latency_ms"] else "-"
        print(
            f"{row['label']} | {row['final_score']:.2f} | {row['quality_idx']:.2f} | {latency_idx_text} | "
            f"{row['avg_score']:.2f} | {row['avg_retry']:.2f} | {row['coverage']:.1f} | "
            f"{row['second_stage']:.2f} | {row['enrichment']:.1f} | {latency_ms_text}"
        )

    best = rows[0]
    print(f"\n[RECOMMEND] {best['label']} (final={best['final_score']:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
