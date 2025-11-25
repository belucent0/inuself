"""화자분리 유틸리티."""
import time
from typing import Any

from . import rocm_env as _rocm_env  # noqa: F401  # sys.path side-effect

import torch
from pyannote.audio import Pipeline as DiarizationPipeline

# HuggingFace Hub 호환성 패치: use_auth_token -> token
# pyannote.audio가 사용하는 오래된 API를 최신 API로 변환
try:
    import huggingface_hub
    import functools
    
    # 원본 함수 백업 (이미 패치된 경우를 대비)
    if not hasattr(huggingface_hub, '_original_hf_hub_download'):
        huggingface_hub._original_hf_hub_download = huggingface_hub.hf_hub_download
    
    # use_auth_token을 token으로 변환하는 래퍼
    @functools.wraps(huggingface_hub._original_hf_hub_download)
    def _patched_hf_hub_download(*args, **kwargs):
        if "use_auth_token" in kwargs:
            kwargs["token"] = kwargs.pop("use_auth_token")
        return huggingface_hub._original_hf_hub_download(*args, **kwargs)
    
    # monkey patch 적용
    huggingface_hub.hf_hub_download = _patched_hf_hub_download
    
    # utils 모듈에도 패치 적용 (pyannote.audio가 사용할 수 있음)
    if hasattr(huggingface_hub, 'utils'):
        if hasattr(huggingface_hub.utils, 'hf_hub_download'):
            huggingface_hub.utils.hf_hub_download = _patched_hf_hub_download
except (ImportError, AttributeError):
    # huggingface_hub가 없거나 이미 다른 방식으로 import된 경우 무시
    pass


def run_diarization(
    waveform: Any,
    sample_rate: int,
    device: str = "cuda",
    audio_duration: float | None = None,
) -> tuple[Any, float, float]:
    """
    화자 분리 실행 (전체 오디오 파일 처리).
    
    Args:
        waveform: 오디오 웨이브폼 데이터 (numpy array)
        sample_rate: 샘플레이트
        device: 디바이스 ("cuda" 또는 "cpu")
        audio_duration: 오디오 길이 (초), 로그용
    
    Returns:
        (diarization_result, load_time, process_time)
    """
    if audio_duration:
        print(f"[Diarization] Starting speaker diarization for entire audio file...")
        print(f"[Diarization] Processing time range: 0.00s - {audio_duration:.2f}s ({audio_duration:.2f}s)")
    else:
        print(f"[Diarization] Starting speaker diarization...")
    
    print(f"[Diarization] Loading speaker diarization model...")
    
    diarization_load_start = time.time()
    diarization_pipeline = DiarizationPipeline.from_pretrained("pyannote/speaker-diarization-3.1")
    diarization_pipeline.to(torch.device(device))
    diarization_load_time = time.time() - diarization_load_start
    
    print(f"[Diarization] Model loaded in {diarization_load_time:.2f} seconds")
    print(f"[Diarization] Starting speaker diarization...")
    
    audio_data = {
        "waveform": torch.from_numpy(waveform).unsqueeze(0).to(device),
        "sample_rate": sample_rate
    }
    
    diarization_start = time.time()
    with torch.inference_mode():
        result = diarization_pipeline(audio_data)
    diarization_time = time.time() - diarization_start
    
    print(f"[Diarization] Completed in {diarization_time:.2f} seconds")
    return result, diarization_load_time, diarization_time


def extract_speaker_segments(diarization_result: Any) -> list[tuple[float, float, str]]:
    """화자 분리 결과에서 세그먼트 추출."""
    segments = []
    for turn, _, speaker in diarization_result.itertracks(yield_label=True):
        segments.append((turn.start, turn.end, speaker))
    segments.sort(key=lambda x: x[0])
    return segments


def compute_speaker_transitions(segments: list[tuple[float, float, str]]) -> list[dict[str, Any]]:
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


def find_optimal_split_points(
    diarization_result: Any,
    audio_duration: float,
    num_chunks: int,
) -> list[float]:
    """화자 분리 결과를 기반으로 여러 분할 지점을 계산한다."""
    if num_chunks <= 1:
        return []
    
    segments = extract_speaker_segments(diarization_result)
    transitions = compute_speaker_transitions(segments)
    search_window = max(5.0, audio_duration * 0.1)
    boundaries = []
    
    for i in range(1, num_chunks):
        target = audio_duration * i / num_chunks
        candidates = [
            t for t in transitions if abs(t["point"] - target) <= search_window
        ]
        if candidates:
            candidates.sort(key=lambda x: (abs(x["point"] - target), -x["gap"]))
            selected = candidates[0]["point"]
            print(
                f"[Split] Using speaker transition near {target:.2f}s -> {selected:.2f}s "
                f"(gap {candidates[0]['gap']:.2f}s)"
            )
        else:
            selected = target
            print(
                f"[Split] No transition near {target:.2f}s, using target as boundary."
            )
        boundaries.append(max(0.0, min(audio_duration, selected)))
    
    # 정렬 및 중복 제거
    unique_boundaries = []
    for point in sorted(boundaries):
        if unique_boundaries and abs(point - unique_boundaries[-1]) < 1e-3:
            continue
        unique_boundaries.append(point)
    return unique_boundaries


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


def merge_segments_with_speakers(
    asr_segments: list[dict[str, Any]],
    diarization_result: Any,
) -> list[dict[str, Any]]:
    """ASR 세그먼트에 화자 정보 추가."""
    # 화자 세그먼트를 딕셔너리로 변환 (빠른 조회)
    speaker_segments = {}
    for turn, _, speaker in diarization_result.itertracks(yield_label=True):
        speaker_segments[(turn.start, turn.end)] = speaker
    
    # 각 ASR 세그먼트에 가장 가까운 화자 할당
    merged_segments = []
    for seg in asr_segments:
        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_mid = (seg_start + seg_end) / 2
        
        # 가장 겹치는 화자 찾기
        best_speaker = None
        max_overlap = 0
        
        for (spk_start, spk_end), speaker in speaker_segments.items():
            # 겹치는 구간 계산
            overlap_start = max(seg_start, spk_start)
            overlap_end = min(seg_end, spk_end)
            overlap = max(0, overlap_end - overlap_start)
            
            if overlap > max_overlap:
                max_overlap = overlap
                best_speaker = speaker
        
        # 세그먼트 중간점이 포함된 화자 찾기 (겹침이 없을 경우)
        if best_speaker is None:
            for (spk_start, spk_end), speaker in speaker_segments.items():
                if spk_start <= seg_mid <= spk_end:
                    best_speaker = speaker
                    break
        
        seg["speaker"] = best_speaker or "UNKNOWN"
        merged_segments.append(seg)
    
    return merged_segments

