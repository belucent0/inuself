"""ASR/화자분리 결과 후처리 유틸리티."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def merge_consecutive_speaker_segments(
    segments: list[dict[str, Any]],
    *,
    max_duration: float = 30.0,
) -> list[dict[str, Any]]:
    """
    동일 화자가 연속으로 발화한 세그먼트를 30초 이하 범위에서 병합한다.

    Args:
        segments: 시간순으로 정렬된 ASR 세그먼트 목록.
        max_duration: 하나의 병합 블록이 가질 수 있는 최대 길이(초).

    Returns:
        병합 처리된 세그먼트 목록.
    """
    if not segments:
        return []

    merged: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for segment in segments:
        if current is None:
            current = deepcopy(segment)
            continue

        same_speaker = segment.get("speaker") == current.get("speaker")
        proposed_duration = segment.get("end", 0.0) - current.get("start", 0.0)

        if same_speaker and proposed_duration <= max_duration:
            _merge_segment(current, segment)
        else:
            merged.append(current)
            current = deepcopy(segment)

    if current is not None:
        merged.append(current)

    return merged


def rebuild_speaker_stats(segments: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    """병합된 세그먼트 기준으로 화자 통계를 다시 계산한다."""
    stats: dict[str, dict[str, float | int]] = {}
    for segment in segments:
        speaker = segment.get("speaker") or "UNKNOWN"
        stats.setdefault(speaker, {"count": 0, "duration": 0.0})
        stats[speaker]["count"] = int(stats[speaker]["count"]) + 1
        stats[speaker]["duration"] = float(stats[speaker]["duration"]) + max(
            0.0,
            segment.get("end", 0.0) - segment.get("start", 0.0),
        )
    return stats


def rebuild_transcription_text(segments: list[dict[str, Any]]) -> str:
    """세그먼트 텍스트를 순서대로 이어 붙여 전체 텍스트를 다시 생성한다."""
    texts = []
    for segment in segments:
        text = (segment.get("text") or "").strip()
        if text:
            texts.append(text)
    return " ".join(texts).strip()


def _merge_segment(base: dict[str, Any], addition: dict[str, Any]) -> None:
    """base 세그먼트에 addition 세그먼트를 합친다 (제자리 작업)."""
    base["end"] = addition.get("end", base.get("end"))
    base["text"] = _join_text(base.get("text"), addition.get("text"))

    list_fields = ("words", "tokens", "timestamps")
    for field in list_fields:
        if field in addition:
            base.setdefault(field, [])
            base[field].extend(addition.get(field, []))


def _join_text(first: str | None, second: str | None) -> str:
    first_clean = (first or "").strip()
    second_clean = (second or "").strip()
    if first_clean and second_clean:
        return f"{first_clean} {second_clean}"
    return first_clean or second_clean









