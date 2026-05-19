"""ASR/화자분리 결과 후처리 유틸리티.

Worker가 ASR + diarization을 결합해 화자 경계 기준으로 segment를 반환하므로
backend는 raw segments를 그대로 사용한다. 이 모듈은 보존된 segments로부터
파생되는 표시용 텍스트와 화자 통계만 재계산한다.
"""

from __future__ import annotations

Segment = dict[str, object]

UNKNOWN_SPEAKER = "UNKNOWN"
SPEAKER_SEPARATOR = " & "  # diarization이 동시 발화를 결합한 라벨 (예: "SPEAKER_00 & SPEAKER_01")


def _seg_float(segment: Segment, key: str, default: float = 0.0) -> float:
    value = segment.get(key, default)
    return float(value) if isinstance(value, (int, float)) else default


def _seg_str(segment: Segment, key: str, default: str = "") -> str:
    value = segment.get(key)
    return value if isinstance(value, str) else default


def rebuild_speaker_stats(
    segments: list[Segment],
) -> dict[str, dict[str, float]]:
    """세그먼트 기준으로 화자별 발화 수/누적 시간을 계산한다."""
    stats: dict[str, dict[str, float]] = {}
    for segment in segments:
        speaker_label = _seg_str(segment, "speaker") or UNKNOWN_SPEAKER
        speakers = (
            speaker_label.split(SPEAKER_SEPARATOR)
            if SPEAKER_SEPARATOR in speaker_label
            else [speaker_label]
        )
        duration = max(0.0, _seg_float(segment, "end") - _seg_float(segment, "start"))

        for speaker in speakers:
            entry = stats.setdefault(speaker, {"count": 0.0, "duration": 0.0})
            entry["count"] += 1
            entry["duration"] += duration
    return stats


def rebuild_transcription_text(segments: list[Segment]) -> str:
    """세그먼트 텍스트를 순서대로 이어 붙여 전체 텍스트를 다시 생성한다."""
    parts = [text for s in segments if (text := _seg_str(s, "text").strip())]
    return " ".join(parts).strip()


def _format_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def segments_to_text_with_metadata(segments: list[Segment]) -> str:
    """세그먼트를 `[SPEAKER] (M:SS-M:SS) 텍스트` 형식으로 변환한다 (LLM 요약용)."""
    lines: list[str] = []
    for segment in segments:
        text = _seg_str(segment, "text").strip()
        if not text:
            continue
        speaker = _seg_str(segment, "speaker") or UNKNOWN_SPEAKER
        start_str = _format_time(_seg_float(segment, "start"))
        end_str = _format_time(_seg_float(segment, "end"))
        lines.append(f"[{speaker}] ({start_str}-{end_str}) {text}")
    return "\n\n".join(lines)
