"""화자분리 유틸리티.

v1.2.0: 실제 화자분리는 ai-diarize 컨테이너(pyannote)에서 수행.
Worker는 ai-gateway 응답 후처리(speaker 라벨 정렬·통합 등)만 담당.
"""
from typing import Any

from worker.logging_config import logger


def extract_speaker_segments(
    diarization_result: Any,
    include_metadata: bool = False,
    split_overlaps: bool = False,
    merge_overlaps: bool = False,
) -> list[tuple[float, float, str] | dict[str, Any]]:
    """
    화자 분리 결과에서 세그먼트 추출.

    Args:
        diarization_result: DiarizationAnnotationWrapper 또는 pyannote Annotation 객체
        include_metadata: True일 경우 딕셔너리 형태로 메타데이터 포함
        split_overlaps: True일 경우 겹치는 구간을 분리하여 별도 세그먼트로 생성
        merge_overlaps: True일 경우 겹치는 구간의 화자 이름을 "A & B" 형태로 병합

    Returns:
        include_metadata=False: [(start, end, speaker), ...]
        include_metadata=True: [{"start": float, "end": float, "speaker": str, "duration": float}, ...]
    """
    segments = []
    iter_count = 0
    for turn, _, speaker in diarization_result.itertracks(yield_label=True):
        iter_count += 1
        if include_metadata:
            segments.append({
                "start": turn.start,
                "end": turn.end,
                "speaker": speaker,
                "duration": turn.end - turn.start,
            })
        else:
            segments.append((turn.start, turn.end, speaker))

    logger.info(f"[extract_speaker_segments] Iterated {iter_count} times, got {len(segments)} segments")

    # 겹치는 구간을 분리하는 경우
    if split_overlaps:
        segments = _split_overlapping_segments(segments, include_metadata)

        # 분리된 구간 중 같은 시간대의 화자들을 병합하는 경우
        if merge_overlaps:
            segments = _create_merged_overlap_segments(segments, include_metadata)

    if include_metadata:
        segments.sort(key=lambda x: x["start"])
    else:
        segments.sort(key=lambda x: x[0])

    return segments


def _split_overlapping_segments(
    segments: list[tuple[float, float, str] | dict[str, Any]],
    include_metadata: bool,
) -> list[tuple[float, float, str] | dict[str, Any]]:
    """겹치는 세그먼트를 분리하여 별도 세그먼트로 생성."""
    if not segments:
        return segments

    # 모든 시간 경계점 수집
    time_points = set()
    for seg in segments:
        if include_metadata:
            time_points.add(seg["start"])
            time_points.add(seg["end"])
        else:
            time_points.add(seg[0])
            time_points.add(seg[1])

    time_points = sorted(time_points)

    # 각 시간 구간에서 활성화된 화자 찾기
    split_segments = []
    for i in range(len(time_points) - 1):
        start_time = time_points[i]
        end_time = time_points[i + 1]

        # 이 구간에서 활성화된 모든 화자 찾기
        active_speakers = []
        for seg in segments:
            if include_metadata:
                seg_start, seg_end, seg_speaker = seg["start"], seg["end"], seg["speaker"]
            else:
                seg_start, seg_end, seg_speaker = seg[0], seg[1], seg[2]

            if seg_start < end_time and seg_end > start_time:
                active_speakers.append(seg_speaker)

        # 각 활성화된 화자에 대해 세그먼트 생성
        for speaker in active_speakers:
            if include_metadata:
                split_segments.append({
                    "start": start_time,
                    "end": end_time,
                    "speaker": speaker,
                    "duration": end_time - start_time,
                    "is_overlap": len(active_speakers) > 1,
                })
            else:
                split_segments.append((start_time, end_time, speaker))

    return split_segments


def _create_merged_overlap_segments(
    segments: list[tuple[float, float, str] | dict[str, Any]],
    include_metadata: bool,
) -> list[tuple[float, float, str] | dict[str, Any]]:
    """겹치는 시간대의 세그먼트들을 하나로 합치고 화자 이름을 병합."""
    if not segments:
        return []

    # 시간대별로 화자들을 그룹화
    time_groups = {}
    for seg in segments:
        if include_metadata:
            start, end, speaker = seg["start"], seg["end"], seg["speaker"]
        else:
            start, end, speaker = seg[0], seg[1], seg[2]

        key = (round(start, 3), round(end, 3))
        if key not in time_groups:
            time_groups[key] = set()
        time_groups[key].add(speaker)

    # 그룹화된 결과를 리스트로 변환
    merged_output = []
    for (start, end), speakers in sorted(time_groups.items()):
        combined_label = " & ".join(sorted(speakers))
        if include_metadata:
            merged_output.append({"start": start, "end": end, "speaker": combined_label})
        else:
            merged_output.append((start, end, combined_label))

    return merged_output


def compute_segment_confidence(
    segment_start: float,
    segment_end: float,
    speaker: str,
    all_segments: list[dict[str, Any]],
    embeddings_dict: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """세그먼트의 신뢰도 지표를 계산."""
    duration = segment_end - segment_start

    # 세그먼트 길이 기반 신뢰도
    length_confidence = min(1.0, duration / 2.0)

    # 인접 세그먼트와의 일관성
    continuity_score = 0.0
    for seg in all_segments:
        if seg.get("speaker") == speaker:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            gap_before = max(0, segment_start - seg_end)
            gap_after = max(0, seg_start - segment_end)
            if gap_before < 1.0 or gap_after < 1.0:
                continuity_score += 1.0

    continuity_confidence = min(1.0, continuity_score / 3.0)

    # Embedding 기반 신뢰도
    embedding_confidence = 0.8 if embeddings_dict and speaker in embeddings_dict else None

    # 종합 신뢰도
    overall_confidence = (
        length_confidence * 0.4 +
        continuity_confidence * 0.4 +
        (embedding_confidence or 0.5) * 0.2
    )

    return {
        "length_confidence": length_confidence,
        "continuity_confidence": continuity_confidence,
        "embedding_confidence": embedding_confidence,
        "overall_confidence": overall_confidence,
        "duration": duration,
    }


def merge_by_diarization_segments(
    asr_segments: list[dict[str, Any]],
    diarization_result: Any,
    min_overlap_ratio: float = 0.3,
) -> list[dict[str, Any]]:
    """
    화자분리 세그먼트 기준으로 ASR 텍스트 매칭 (V2 로직).

    핵심 철학: 화자분리가 "음성 존재의 ground truth" 역할
    - 화자분리가 음성 없다고 판단한 구간은 결과에 포함하지 않음
    - Whisper 환각이 자연스럽게 필터링됨

    Args:
        asr_segments: ASR 결과 세그먼트 리스트 [{start, end, text}, ...]
        diarization_result: DiarizationAnnotationWrapper 객체
        min_overlap_ratio: ASR 세그먼트가 화자분리 구간에 포함되기 위한 최소 겹침 비율

    Returns:
        화자분리 기준으로 재구성된 세그먼트 리스트
    """
    logger.info(f"[MergeV2] ASR segments: {len(asr_segments)}")
    logger.info(f"[MergeV2] Diarization type: {type(diarization_result).__name__}")

    # 화자분리 세그먼트 추출 (겹침 분리 + 병합)
    diarization_segments = extract_speaker_segments(
        diarization_result,
        include_metadata=True,
        split_overlaps=True,
        merge_overlaps=True,
    )

    if not diarization_segments:
        logger.warning("[MergeV2] No diarization segments, returning ASR as-is with UNKNOWN speaker")
        for seg in asr_segments:
            seg["speaker"] = "UNKNOWN"
            seg["overlap_ratio"] = 0.0
        return asr_segments

    logger.info(f"[MergeV2] Diarization segments: {len(diarization_segments)}")

    # 화자분리의 마지막 음성 종료 시점 (환각 필터링 기준)
    last_diarization_end = max(seg["end"] for seg in diarization_segments)
    logger.info(f"[MergeV2] Last diarization end: {last_diarization_end:.2f}s")

    results = []
    used_asr_indices = set()  # 이미 사용된 ASR 세그먼트 추적

    for diar_seg in diarization_segments:
        diar_start = diar_seg["start"]
        diar_end = diar_seg["end"]
        speaker = diar_seg["speaker"]
        diar_duration = diar_end - diar_start

        # 이 화자분리 구간에 겹치는 ASR 세그먼트 찾기
        matched_texts = []
        total_overlap = 0.0

        for idx, asr_seg in enumerate(asr_segments):
            asr_start = asr_seg["start"]
            asr_end = asr_seg["end"]
            asr_duration = asr_end - asr_start

            # 겹침 계산
            overlap_start = max(diar_start, asr_start)
            overlap_end = min(diar_end, asr_end)
            overlap = max(0, overlap_end - overlap_start)

            if overlap <= 0:
                continue

            # ASR 세그먼트 기준 겹침 비율
            asr_overlap_ratio = overlap / asr_duration if asr_duration > 0 else 0

            # 최소 겹침 비율 이상인 경우만 매칭
            if asr_overlap_ratio >= min_overlap_ratio:
                text = asr_seg.get("text", "").strip()
                if text:
                    matched_texts.append(text)
                    total_overlap += overlap
                    used_asr_indices.add(idx)

        # 매칭된 텍스트가 있으면 결과에 추가
        if matched_texts:
            combined_text = " ".join(matched_texts)
            overlap_ratio = total_overlap / diar_duration if diar_duration > 0 else 0

            results.append({
                "start": diar_start,
                "end": diar_end,
                "speaker": speaker,
                "text": combined_text,
                "overlap_ratio": min(1.0, overlap_ratio),
                "duration": diar_duration,
            })

    # 사용되지 않은 ASR 세그먼트 로깅 (환각 또는 누락된 세그먼트)
    unused_count = len(asr_segments) - len(used_asr_indices)
    if unused_count > 0:
        logger.info(f"[MergeV2] Filtered {unused_count} ASR segments (no diarization match)")
        for idx, asr_seg in enumerate(asr_segments):
            if idx not in used_asr_indices:
                logger.debug(
                    f"[MergeV2] Filtered: [{asr_seg['start']:.2f}s - {asr_seg['end']:.2f}s] "
                    f"'{asr_seg.get('text', '')[:50]}...'"
                )

    logger.info(f"[MergeV2] Result segments: {len(results)}")
    return results


def merge_segments_with_speakers(
    asr_segments: list[dict[str, Any]],
    diarization_result: Any,
    embeddings_dict: dict[str, list[float]] | None = None,
    split_overlaps: bool = True,
) -> list[dict[str, Any]]:
    """
    ASR 세그먼트에 화자 정보 추가.

    Args:
        asr_segments: ASR 결과 세그먼트 리스트
        diarization_result: DiarizationAnnotationWrapper 객체
        embeddings_dict: 화자별 embedding 딕셔너리 (선택적)
        split_overlaps: True일 경우 겹치는 구간을 분리하여 처리
    """
    logger.info(f"[Merging] ASR segments: {len(asr_segments)}")
    logger.info(f"[Merging] Diarization type: {type(diarization_result).__name__}")

    # 화자 세그먼트를 딕셔너리로 변환
    speaker_segments = {}
    if split_overlaps:
        split_diarization_segments = extract_speaker_segments(
            diarization_result,
            include_metadata=True,
            split_overlaps=True,
            merge_overlaps=True,
        )
        logger.info(f"[Merging] Split diarization segments: {len(split_diarization_segments)}")
        for seg in split_diarization_segments:
            speaker_segments[(seg["start"], seg["end"])] = seg["speaker"]
    else:
        for turn, _, speaker in diarization_result.itertracks(yield_label=True):
            speaker_segments[(turn.start, turn.end)] = speaker

    logger.info(f"[Merging] Speaker segments dict size: {len(speaker_segments)}")

    # 모든 화자 세그먼트 리스트 (신뢰도 계산용)
    all_diarization_segments = extract_speaker_segments(
        diarization_result,
        include_metadata=True,
        split_overlaps=split_overlaps,
    )

    # 각 ASR 세그먼트에 가장 가까운 화자 할당
    merged_segments = []
    for seg in asr_segments:
        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_mid = (seg_start + seg_end) / 2

        # 가장 겹치는 화자 찾기
        best_speaker = None
        max_overlap = 0
        best_overlap_ratio = 0.0

        for (spk_start, spk_end), speaker in speaker_segments.items():
            overlap_start = max(seg_start, spk_start)
            overlap_end = min(seg_end, spk_end)
            overlap = max(0, overlap_end - overlap_start)

            seg_duration = seg_end - seg_start
            overlap_ratio = overlap / seg_duration if seg_duration > 0 else 0

            if overlap > max_overlap:
                max_overlap = overlap
                best_speaker = speaker
                best_overlap_ratio = overlap_ratio

        # 세그먼트 중간점이 포함된 화자 찾기 (겹침이 없을 경우)
        if best_speaker is None:
            for (spk_start, spk_end), speaker in speaker_segments.items():
                if spk_start <= seg_mid <= spk_end:
                    best_speaker = speaker
                    best_overlap_ratio = 0.5
                    break

        # 화자를 찾지 못한 세그먼트는 UNKNOWN으로 할당 (Diarization 실패 시에도 세그먼트 유지)
        if best_speaker is None:
            best_speaker = "UNKNOWN"
            best_overlap_ratio = 0.0
            logger.debug(
                f"[Merging] No speaker match, assigning UNKNOWN: "
                f"{seg_start:.2f}s ~ {seg_end:.2f}s"
            )

        # 화자 할당
        seg["speaker"] = best_speaker

        # 신뢰도 메타데이터 추가
        confidence_meta = compute_segment_confidence(
            seg_start, seg_end, best_speaker,
            all_diarization_segments, embeddings_dict,
        )
        confidence_meta["overlap_ratio"] = best_overlap_ratio
        confidence_meta["overall_confidence"] = (
            confidence_meta["overall_confidence"] * 0.7 + best_overlap_ratio * 0.3
        )
        seg.update(confidence_meta)

        merged_segments.append(seg)

    return merged_segments


# Legacy functions for compatibility (V5에서는 사용하지 않음)
def build_nominal_ranges(audio_duration: float, boundary_points: list[float]) -> list[tuple[float, float]]:
    """분할 지점 리스트를 기반으로 (start, end) 구간 리스트 생성."""
    ranges = []
    prev = 0.0
    for boundary in boundary_points:
        boundary = max(prev + 1e-3, min(audio_duration - 1e-3, boundary))
        ranges.append((prev, boundary))
        prev = boundary
    ranges.append((prev, audio_duration))
    return ranges


def find_optimal_split_points(
    diarization_result: Any,
    audio_duration: float,
    num_chunks: int,
) -> list[float]:
    """화자 분리 결과를 기반으로 여러 분할 지점을 계산."""
    if num_chunks <= 1:
        return []

    segments = extract_speaker_segments(diarization_result)
    transitions = _compute_speaker_transitions(segments)
    search_window = max(5.0, audio_duration * 0.1)
    boundaries = []

    for i in range(1, num_chunks):
        target = audio_duration * i / num_chunks
        candidates = [t for t in transitions if abs(t["point"] - target) <= search_window]
        if candidates:
            candidates.sort(key=lambda x: (abs(x["point"] - target), -x["gap"]))
            selected = candidates[0]["point"]
        else:
            selected = target
        boundaries.append(max(0.0, min(audio_duration, selected)))

    return sorted(set(boundaries))


def _compute_speaker_transitions(segments: list[tuple[float, float, str]]) -> list[dict[str, Any]]:
    """화자 전환 지점 계산."""
    transitions = []
    for i in range(len(segments) - 1):
        current_speaker = segments[i][2]
        next_speaker = segments[i + 1][2]
        if current_speaker == next_speaker:
            continue
        transition_start = segments[i][1]
        transition_end = segments[i + 1][0]
        center = (transition_start + transition_end) / 2
        gap = max(0.0, transition_end - transition_start)
        transitions.append({
            "point": center,
            "gap": gap,
            "start": transition_start,
            "end": transition_end,
        })
    return transitions
