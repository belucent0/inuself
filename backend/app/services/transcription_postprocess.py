"""ASR/화자분리 결과 후처리 유틸리티."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def split_long_segments(
    segments: list[dict[str, Any]],
    *,
    max_duration: float = 30.0,
) -> list[dict[str, Any]]:
    """
    긴 세그먼트를 지정된 최대 길이로 분할한다.
    
    FLM 등 세그먼트가 없거나 매우 긴 경우에 대응하기 위한 공통 후처리.
    whisper.cpp처럼 이미 적절히 분할된 경우에는 영향 없음.
    
    Args:
        segments: ASR 세그먼트 목록.
        max_duration: 세그먼트 최대 길이(초). 이 값을 초과하면 분할.
    
    Returns:
        분할 처리된 세그먼트 목록.
    """
    if not segments:
        return []
    
    result: list[dict[str, Any]] = []
    
    for segment in segments:
        seg_start = segment.get("start", 0.0)
        seg_end = segment.get("end", 0.0)
        duration = seg_end - seg_start
        
        if duration <= max_duration:
            # 세그먼트가 충분히 짧으면 그대로 추가
            result.append(deepcopy(segment))
        else:
            # 긴 세그먼트를 max_duration 단위로 분할
            # 텍스트는 분할하지 않고 첫 세그먼트에만 포함 (FLM 특성상 세그먼트별 텍스트가 이미 있음)
            text = segment.get("text", "")
            speaker = segment.get("speaker")
            
            current_start = seg_start
            is_first = True
            
            while current_start < seg_end:
                current_end = min(current_start + max_duration, seg_end)
                
                new_segment = {
                    "start": current_start,
                    "end": current_end,
                    "text": text if is_first else "",  # 첫 세그먼트에만 텍스트
                }
                if speaker:
                    new_segment["speaker"] = speaker
                
                # 원본 세그먼트의 다른 필드도 복사 (첫 세그먼트에만)
                if is_first:
                    for key, value in segment.items():
                        if key not in ("start", "end", "text", "speaker"):
                            new_segment[key] = deepcopy(value)
                
                result.append(new_segment)
                current_start = current_end
                is_first = False
    
    return result


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
        speaker_label = segment.get("speaker") or "UNKNOWN"
        
        # " & "로 화자 분리 (예: "SPEAKER_00 & SPEAKER_01")
        if " & " in speaker_label:
            speakers = speaker_label.split(" & ")
        else:
            speakers = [speaker_label]
            
        duration = max(
            0.0,
            segment.get("end", 0.0) - segment.get("start", 0.0),
        )

        for speaker in speakers:
            stats.setdefault(speaker, {"count": 0, "duration": 0.0})
            stats[speaker]["count"] = int(stats[speaker]["count"]) + 1
            stats[speaker]["duration"] = float(stats[speaker]["duration"]) + duration
    return stats


def rebuild_transcription_text(segments: list[dict[str, Any]]) -> str:
    """세그먼트 텍스트를 순서대로 이어 붙여 전체 텍스트를 다시 생성한다."""
    texts = []
    for segment in segments:
        text = (segment.get("text") or "").strip()
        if text:
            texts.append(text)
    return " ".join(texts).strip()


def _format_time(seconds: float) -> str:
    """초를 M:SS 형식으로 변환."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def segments_to_text_with_metadata(segments: list[dict[str, Any]]) -> str:
    """세그먼트를 화자/시간 정보 포함 텍스트로 변환한다.

    LLM 요약 시 화자 구분과 시간 컨텍스트를 제공하기 위해 사용.

    Args:
        segments: ASR 세그먼트 목록.

    Returns:
        "[SPEAKER_00] (0:00-0:15) 텍스트..." 형식의 문자열.
    """
    if not segments:
        return ""

    lines = []
    for segment in segments:
        text = (segment.get("text") or "").strip()
        if not text:
            continue

        speaker = segment.get("speaker") or "UNKNOWN"
        start = segment.get("start", 0.0)
        end = segment.get("end", 0.0)

        start_str = _format_time(start)
        end_str = _format_time(end)

        lines.append(f"[{speaker}] ({start_str}-{end_str}) {text}")

    return "\n\n".join(lines)


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
















