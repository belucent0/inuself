#!/usr/bin/env python3
"""Fail CI when SearchQuality metrics regress beyond configured thresholds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_THRESHOLDS = {
    "min_relative": {
        "avg_score": 0.95,
        "avg_quality_score": 0.95,
        "avg_content_coverage_ratio": 0.90,
        "avg_second_stage_score": 0.90,
    },
    "max_relative": {
        "avg_retry_count": 1.25,
        "low_content_coverage_rate": 1.25,
    },
    "absolute_floor": {
        "avg_result_count": 1.0,
    },
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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
        entry
        for entry in evaluations
        if isinstance(entry, dict) and entry.get("evaluator_name") == "SearchQuality"
    ]
    if not search_evals:
        return {}

    scores = [_safe_float(entry.get("score"), 0.0) for entry in search_evals]
    metrics_list = [
        entry.get("metrics", {})
        for entry in search_evals
        if isinstance(entry.get("metrics", {}), dict)
    ]

    avg_quality = _avg(
        [
            _safe_float(metrics.get("avg_quality_score"), 0.0)
            for metrics in metrics_list
        ],
        0.0,
    )
    avg_results = _avg(
        [_safe_float(metrics.get("avg_result_count"), 0.0) for metrics in metrics_list],
        0.0,
    )
    avg_retry = _avg(
        [_safe_float(metrics.get("avg_retry_count"), 0.0) for metrics in metrics_list],
        0.0,
    )
    avg_content_coverage = _avg(
        [
            _safe_float(metrics.get("avg_content_coverage_ratio"), 0.5)
            for metrics in metrics_list
        ],
        0.5,
    )
    avg_second_stage = _avg(
        [
            _safe_float(metrics.get("avg_second_stage_score"), 50.0)
            for metrics in metrics_list
        ],
        50.0,
    )

    low_content_coverage_rates: list[float] = []
    for metrics in metrics_list:
        retry_distribution = metrics.get("retry_reason_distribution", {})
        if isinstance(retry_distribution, dict):
            low_content_coverage_rates.append(
                _safe_float(retry_distribution.get("low_content_coverage"), 0.0)
            )

    return {
        "avg_score": _avg(scores, 0.0),
        "avg_quality_score": avg_quality,
        "avg_result_count": avg_results,
        "avg_retry_count": avg_retry,
        "avg_content_coverage_ratio": avg_content_coverage,
        "avg_second_stage_score": avg_second_stage,
        "low_content_coverage_rate": _avg(low_content_coverage_rates, 0.0),
    }


def _ratio(candidate: float, baseline: float) -> float:
    if baseline <= 0:
        return 1.0 if candidate >= 0 else 0.0
    return candidate / baseline


def _evaluate_rules(
    baseline_stats: dict[str, float],
    candidate_stats: dict[str, float],
    thresholds: dict[str, Any],
) -> tuple[list[str], list[str]]:
    passes: list[str] = []
    failures: list[str] = []

    min_relative = thresholds.get("min_relative", {})
    max_relative = thresholds.get("max_relative", {})
    absolute_floor = thresholds.get("absolute_floor", {})

    for metric, min_ratio in min_relative.items():
        baseline = baseline_stats.get(metric, 0.0)
        candidate = candidate_stats.get(metric, 0.0)
        observed_ratio = _ratio(candidate, baseline)
        message = (
            f"{metric}: baseline={baseline:.4f}, candidate={candidate:.4f}, "
            f"ratio={observed_ratio:.4f}, required>={float(min_ratio):.4f}"
        )
        if observed_ratio >= float(min_ratio):
            passes.append(message)
        else:
            failures.append(message)

    for metric, max_ratio in max_relative.items():
        baseline = baseline_stats.get(metric, 0.0)
        candidate = candidate_stats.get(metric, 0.0)
        observed_ratio = _ratio(candidate, baseline)
        message = (
            f"{metric}: baseline={baseline:.4f}, candidate={candidate:.4f}, "
            f"ratio={observed_ratio:.4f}, required<={float(max_ratio):.4f}"
        )
        if observed_ratio <= float(max_ratio):
            passes.append(message)
        else:
            failures.append(message)

    for metric, floor in absolute_floor.items():
        candidate = candidate_stats.get(metric, 0.0)
        message = f"{metric}: candidate={candidate:.4f}, required>={float(floor):.4f}"
        if candidate >= float(floor):
            passes.append(message)
        else:
            failures.append(message)

    return passes, failures


def _write_summary(
    path: Path,
    baseline_path: Path,
    candidate_path: Path,
    baseline_stats: dict[str, float],
    candidate_stats: dict[str, float],
    passes: list[str],
    failures: list[str],
) -> None:
    lines = [
        "# Quality Regression Gate",
        "",
        f"- baseline: `{baseline_path}`",
        f"- candidate: `{candidate_path}`",
        "",
        "## Baseline Stats",
    ]

    for key in sorted(baseline_stats):
        lines.append(f"- {key}: {baseline_stats[key]:.4f}")

    lines.extend(["", "## Candidate Stats"])
    for key in sorted(candidate_stats):
        lines.append(f"- {key}: {candidate_stats[key]:.4f}")

    lines.extend(["", "## Rule Results"])
    for message in passes:
        lines.append(f"- PASS: {message}")
    for message in failures:
        lines.append(f"- FAIL: {message}")

    lines.extend(["", f"## Final: {'PASS' if not failures else 'FAIL'}", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Search quality regression gate")
    parser.add_argument("--baseline", required=True, help="Baseline report JSON path")
    parser.add_argument("--candidate", required=True, help="Candidate report JSON path")
    parser.add_argument(
        "--thresholds",
        default=None,
        help="Threshold JSON path (optional, defaults to built-in values)",
    )
    parser.add_argument(
        "--summary-path",
        default=None,
        help="Optional markdown summary output path",
    )
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    candidate_path = Path(args.candidate)
    if not baseline_path.exists():
        print(f"[ERROR] baseline file not found: {baseline_path}")
        return 1
    if not candidate_path.exists():
        print(f"[ERROR] candidate file not found: {candidate_path}")
        return 1

    thresholds = DEFAULT_THRESHOLDS
    if args.thresholds:
        threshold_path = Path(args.thresholds)
        if not threshold_path.exists():
            print(f"[ERROR] threshold file not found: {threshold_path}")
            return 1
        thresholds = _load_json(threshold_path)

    baseline_report = _load_json(baseline_path)
    candidate_report = _load_json(candidate_path)
    baseline_stats = _extract_search_quality_stats(baseline_report)
    candidate_stats = _extract_search_quality_stats(candidate_report)

    if not baseline_stats:
        print("[ERROR] baseline SearchQuality stats are missing")
        return 1
    if not candidate_stats:
        print("[ERROR] candidate SearchQuality stats are missing")
        return 1

    passes, failures = _evaluate_rules(baseline_stats, candidate_stats, thresholds)

    print("Search quality regression gate")
    print(f"- baseline:  {baseline_path}")
    print(f"- candidate: {candidate_path}")
    print(f"- checks passed: {len(passes)}")
    print(f"- checks failed: {len(failures)}")

    for message in failures:
        print(f"[FAIL] {message}")

    if args.summary_path:
        summary_path = Path(args.summary_path)
        _write_summary(
            summary_path,
            baseline_path,
            candidate_path,
            baseline_stats,
            candidate_stats,
            passes,
            failures,
        )
        print(f"[INFO] summary: {summary_path}")

    if failures:
        print("[ERROR] Regression gate failed")
        return 1

    print("[OK] Regression gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
