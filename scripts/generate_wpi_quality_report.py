#!/usr/bin/env python3
"""Generate deterministic WPI report quality scores from fixture cases."""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SectionRule:
    title: str
    min_chars: int
    min_bullets: int


DEFAULT_RULES: dict[str, Any] = {
    "sections": [
        {"title": "종합 해석", "min_chars": 360, "min_bullets": 0},
        {"title": "핵심 강점", "min_chars": 320, "min_bullets": 4},
        {"title": "주의 포인트", "min_chars": 320, "min_bullets": 4},
        {"title": "상황별 제안", "min_chars": 420, "min_bullets": 6},
        {"title": "비우세형(보조 프로파일) 해석", "min_chars": 280, "min_bullets": 0},
        {"title": "코멘트", "min_chars": 160, "min_bullets": 0},
    ],
    "actions_subsections": {
        "개인 실행": 3,
        "협업/소통": 3,
    },
    "evidence_min_ratio": 0.45,
    "overall_pass_score": 80.0,
    "numeric_tolerance": 0.2,
    "safety_forbidden_phrases": [
        "이것이 정답입니다",
        "진단된다",
        "임상적으로 확정",
        "문제가 있는 성격",
        "치료가 필요",
        "확정된다",
        "반드시 실패",
    ],
    "weights": {
        "structure": 20.0,
        "length": 20.0,
        "bullets": 20.0,
        "actions": 10.0,
        "evidence": 15.0,
        "numeric": 10.0,
        "safety": 5.0,
    },
}

HEADING_PATTERN = re.compile(r"^##\s*(\d+)\.\s*(.+?)\s*$", re.MULTILINE)
FLOAT_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?")
EVIDENCE_PATTERN = re.compile(
    r"(점수|갭|축|우세형|보조형|프로파일|\d+(?:\.\d+)?점|i-test|me-test)",
    re.IGNORECASE,
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rules(path: Path | None) -> dict[str, Any]:
    if path is None:
        return DEFAULT_RULES
    loaded = _load_json(path)
    if not isinstance(loaded, dict):
        raise ValueError("Rules file must be a JSON object")
    merged = dict(DEFAULT_RULES)
    merged.update(loaded)
    return merged


def _load_cases(path: Path) -> list[dict[str, Any]]:
    loaded = _load_json(path)
    if not isinstance(loaded, list):
        raise ValueError("Fixture file must be a list")

    cases: list[dict[str, Any]] = []
    for item in loaded:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id", "")).strip()
        report_md_value = item.get("report_md")
        report_md: str | None = None

        if isinstance(report_md_value, str) and report_md_value.strip():
            report_md = report_md_value
        else:
            report_lines = item.get("report_lines")
            if isinstance(report_lines, list):
                normalized = [str(line) for line in report_lines if str(line).strip()]
                if normalized:
                    report_md = "\n".join(normalized)

        if not case_id or not isinstance(report_md, str) or not report_md.strip():
            continue
        context = item.get("context")
        expected_pass = item.get("expected_pass")
        cases.append(
            {
                "id": case_id,
                "report_md": report_md,
                "context": context if isinstance(context, dict) else {},
                "expected_pass": expected_pass
                if isinstance(expected_pass, bool)
                else None,
            }
        )

    if not cases:
        raise ValueError("No valid WPI report cases found")
    return cases


def _to_section_rules(raw_rules: list[dict[str, Any]]) -> list[SectionRule]:
    section_rules: list[SectionRule] = []
    for entry in raw_rules:
        section_rules.append(
            SectionRule(
                title=str(entry["title"]),
                min_chars=int(entry["min_chars"]),
                min_bullets=int(entry["min_bullets"]),
            )
        )
    return section_rules


def _extract_sections(markdown: str) -> tuple[list[str], dict[str, str]]:
    matches = list(HEADING_PATTERN.finditer(markdown))
    if not matches:
        return [], {}

    ordered_titles: list[str] = []
    sections: dict[str, str] = {}

    for index, match in enumerate(matches):
        title = match.group(2).strip()
        body_start = match.end()
        body_end = (
            matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        )
        body = markdown[body_start:body_end].strip()
        ordered_titles.append(title)
        sections[title] = body

    return ordered_titles, sections


def _count_bullets(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip().startswith("- "))


def _split_sentences(text: str) -> list[str]:
    chunks = re.split(r"\n+|(?<=[!?])\s+|(?<=\.)\s+", text)
    return [chunk.strip() for chunk in chunks if len(chunk.strip()) >= 8]


def _evaluate_actions_section(
    content: str, actions_rules: dict[str, int]
) -> tuple[bool, dict[str, int]]:
    current: str | None = None
    counts = {title: 0 for title in actions_rules}

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("### "):
            heading = line.replace("### ", "", 1).strip()
            current = heading if heading in actions_rules else None
            continue
        if line.startswith("- ") and current:
            counts[current] += 1

    passed = all(counts[key] >= actions_rules[key] for key in actions_rules)
    return passed, counts


def _extract_gap_claims(report_md: str) -> list[tuple[float, float, float]]:
    claims: list[tuple[float, float, float]] = []
    for sentence in _split_sentences(report_md):
        if "갭" not in sentence:
            continue
        numbers = [float(value) for value in FLOAT_PATTERN.findall(sentence)]
        if len(numbers) < 3:
            continue
        claims.append((numbers[0], numbers[1], numbers[2]))
    return claims


def _evaluate_numeric_consistency(
    report_md: str, tolerance: float
) -> tuple[float, int, int]:
    claims = _extract_gap_claims(report_md)
    if not claims:
        return 100.0, 0, 0

    valid = 0
    for left, right, claimed_gap in claims:
        diff = round(left - right, 1)
        if abs(claimed_gap - diff) <= tolerance or abs(claimed_gap + diff) <= tolerance:
            valid += 1

    score = (valid / len(claims)) * 100.0
    return score, valid, len(claims)


def _evaluate_case(case: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    report_md = str(case["report_md"])
    section_rules = _to_section_rules(rules["sections"])
    expected_titles = [rule.title for rule in section_rules]
    weights = dict(rules["weights"])

    ordered_titles, sections = _extract_sections(report_md)
    order_matches = ordered_titles == expected_titles
    section_presence = sum(1 for title in expected_titles if title in sections)
    structure_score = (
        100.0
        if order_matches
        else (section_presence / len(expected_titles)) * 100.0
        if expected_titles
        else 0.0
    )

    length_ratios: list[float] = []
    bullet_ratios: list[float] = []
    issues: list[str] = []

    for rule in section_rules:
        body = sections.get(rule.title, "")
        body_len = len(body)
        if body_len < rule.min_chars:
            issues.append(
                f"섹션 '{rule.title}' 최소 길이 미달 ({body_len}/{rule.min_chars})"
            )
        length_ratios.append(
            min(body_len / rule.min_chars, 1.0) if rule.min_chars > 0 else 1.0
        )

        if rule.min_bullets > 0:
            bullets = _count_bullets(body)
            if bullets < rule.min_bullets:
                issues.append(
                    f"섹션 '{rule.title}' 불릿 수 부족 ({bullets}/{rule.min_bullets})"
                )
            bullet_ratios.append(
                min(bullets / rule.min_bullets, 1.0) if rule.min_bullets > 0 else 1.0
            )

    length_score = (
        (sum(length_ratios) / len(length_ratios)) * 100.0 if length_ratios else 0.0
    )
    bullet_score = (
        (sum(bullet_ratios) / len(bullet_ratios)) * 100.0 if bullet_ratios else 0.0
    )

    actions_content = sections.get("상황별 제안", "")
    actions_passed, action_counts = _evaluate_actions_section(
        actions_content,
        dict(rules["actions_subsections"]),
    )
    actions_score = 100.0 if actions_passed else 0.0
    if not actions_passed:
        issues.append(
            "상황별 제안 섹션에서 '### 개인 실행'/'### 협업/소통' 하위 불릿 수가 부족합니다"
        )

    evidence_sentences = [
        sentence
        for sentence in _split_sentences(report_md)
        if EVIDENCE_PATTERN.search(sentence)
    ]
    total_sentences = _split_sentences(report_md)
    evidence_ratio = (
        len(evidence_sentences) / len(total_sentences) if total_sentences else 0.0
    )
    evidence_target = float(rules["evidence_min_ratio"])
    evidence_score = (
        min(evidence_ratio / evidence_target, 1.0) * 100.0
        if evidence_target > 0
        else 100.0
    )
    if evidence_ratio < evidence_target:
        issues.append(
            f"근거 포함 문장 비율이 낮습니다 ({evidence_ratio:.2f} < {evidence_target:.2f})"
        )

    numeric_score, numeric_valid, numeric_total = _evaluate_numeric_consistency(
        report_md,
        float(rules["numeric_tolerance"]),
    )
    if numeric_total > 0 and numeric_valid < numeric_total:
        issues.append(
            f"갭 수치 정합성 불일치 ({numeric_valid}/{numeric_total} 문장 일치)"
        )

    forbidden_phrases = [
        phrase
        for phrase in list(rules["safety_forbidden_phrases"])
        if phrase and phrase in report_md
    ]
    safety_score = max(0.0, 100.0 - (len(forbidden_phrases) * 25.0))
    if forbidden_phrases:
        issues.append(f"금지 표현 감지: {', '.join(forbidden_phrases)}")

    weighted_score = (
        structure_score * float(weights["structure"]) / 100.0
        + length_score * float(weights["length"]) / 100.0
        + bullet_score * float(weights["bullets"]) / 100.0
        + actions_score * float(weights["actions"]) / 100.0
        + evidence_score * float(weights["evidence"]) / 100.0
        + numeric_score * float(weights["numeric"]) / 100.0
        + safety_score * float(weights["safety"]) / 100.0
    )

    hard_gate_pass = (
        order_matches
        and section_presence == len(expected_titles)
        and actions_passed
        and not forbidden_phrases
        and (numeric_total == 0 or numeric_score >= 90.0)
    )
    passed = hard_gate_pass and weighted_score >= float(rules["overall_pass_score"])

    metrics = {
        "structure_score": round(structure_score, 2),
        "length_score": round(length_score, 2),
        "bullet_score": round(bullet_score, 2),
        "actions_score": round(actions_score, 2),
        "evidence_score": round(evidence_score, 2),
        "evidence_ratio": round(evidence_ratio, 4),
        "numeric_score": round(numeric_score, 2),
        "numeric_claim_valid": numeric_valid,
        "numeric_claim_total": numeric_total,
        "safety_score": round(safety_score, 2),
        "forbidden_phrase_count": len(forbidden_phrases),
        "section_presence": section_presence,
        "expected_sections": len(expected_titles),
        "actions_counts": action_counts,
        "order_matches": order_matches,
        "hard_gate_pass": hard_gate_pass,
    }

    return {
        "conversation_id": case["id"],
        "evaluator_name": "WpiReportQuality",
        "score": round(weighted_score, 2),
        "metrics": metrics,
        "passed": passed,
        "issues": issues,
        "timestamp": time.time(),
        "expected_pass": case.get("expected_pass"),
    }


def _build_summary(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(evaluations)
    passed = sum(1 for evaluation in evaluations if evaluation.get("passed"))
    scores = [float(evaluation.get("score", 0.0)) for evaluation in evaluations]
    avg_score = sum(scores) / total if total else 0.0
    pass_rate = (passed / total * 100.0) if total else 0.0

    return {
        "total_evaluations": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": pass_rate,
        "by_evaluator": {
            "WpiReportQuality": {
                "total": total,
                "passed": passed,
                "avg_score": avg_score,
                "pass_rate": pass_rate,
            }
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic WPI report quality report"
    )
    parser.add_argument(
        "--cases",
        required=True,
        help="Fixture JSON path (e.g. .ci/quality/fixtures/wpi_report_quality_cases.json)",
    )
    parser.add_argument("--output", required=True, help="Output JSON report path")
    parser.add_argument(
        "--rules",
        default=None,
        help="Optional rules JSON path. If omitted, built-in defaults are used.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when case.expected_pass does not match measured pass/fail",
    )
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.exists():
        print(f"[ERROR] fixture file not found: {cases_path}")
        return 1

    rules_path = Path(args.rules) if args.rules else None
    if rules_path and not rules_path.exists():
        print(f"[ERROR] rules file not found: {rules_path}")
        return 1

    rules = _load_rules(rules_path)
    cases = _load_cases(cases_path)

    evaluations = [_evaluate_case(case, rules) for case in cases]
    summary = _build_summary(evaluations)

    expectation_mismatches: list[str] = []
    if args.strict:
        for entry in evaluations:
            expected = entry.get("expected_pass")
            if isinstance(expected, bool) and bool(entry.get("passed")) != expected:
                expectation_mismatches.append(
                    f"{entry.get('conversation_id')}: expected_pass={expected}, actual={entry.get('passed')}"
                )

    report = {
        "test_run_id": str(uuid.uuid4()),
        "timestamp": time.time(),
        "conversations_tested": len(cases),
        "evaluations": evaluations,
        "summary": summary,
        "config": {
            "source": "wpi-quality-fixture",
            "fixture_path": str(cases_path),
            "rules_path": str(rules_path) if rules_path else "<default>",
            "strict": bool(args.strict),
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
    print(
        f"[INFO] avg_score: {summary['by_evaluator']['WpiReportQuality']['avg_score']:.1f}"
    )
    print(f"[INFO] pass_rate: {summary['pass_rate']:.1f}%")

    if expectation_mismatches:
        print("[ERROR] strict expectation mismatch detected")
        for mismatch in expectation_mismatches:
            print(f"  - {mismatch}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
