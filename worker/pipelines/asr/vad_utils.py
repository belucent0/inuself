"""VAD (Voice Activity Detection) 유틸리티.

에너지 기반 음성 구간 감지 및 청킹 기능 제공.
FLM(NPU) ASR에서 세그먼트 타임스탬프 생성에 사용.
"""
import numpy as np
from typing import Any

from worker.logging_config import logger


def get_speech_timestamps_energy(
    waveform: np.ndarray,
    sample_rate: int = 16000,
    min_speech_duration_ms: int = 250,
    min_silence_duration_ms: int = 300,
    threshold_ratio: float = 0.1,
    window_size_ms: int = 30,
) -> list[dict[str, float]]:
    """
    에너지 기반 간단한 VAD (Voice Activity Detection).

    Args:
        waveform: 오디오 웨이브폼 (numpy array)
        sample_rate: 샘플레이트
        min_speech_duration_ms: 최소 음성 구간 길이 (ms)
        min_silence_duration_ms: 최소 묵음 구간 길이 (ms)
        threshold_ratio: 에너지 임계값 비율 (최대 에너지 대비)
        window_size_ms: 윈도우 크기 (ms)

    Returns:
        음성 구간 리스트 [{"start": float, "end": float}, ...]
    """
    # 윈도우 크기 계산
    window_size = int(sample_rate * window_size_ms / 1000)
    hop_size = window_size // 2

    # RMS 에너지 계산
    energies = []
    for i in range(0, len(waveform) - window_size, hop_size):
        window = waveform[i:i + window_size]
        rms = np.sqrt(np.mean(window ** 2))
        energies.append(rms)

    energies = np.array(energies)

    if len(energies) == 0:
        return [{"start": 0.0, "end": len(waveform) / sample_rate}]

    # 임계값 설정 (상위 에너지의 비율)
    threshold = np.percentile(energies, 90) * threshold_ratio

    # 음성/묵음 구간 감지
    is_speech = energies > threshold

    # 음성 구간 추출
    speech_segments = []
    in_speech = False
    speech_start = 0

    min_speech_frames = int(min_speech_duration_ms / window_size_ms * 2)
    min_silence_frames = int(min_silence_duration_ms / window_size_ms * 2)

    silence_count = 0
    speech_count = 0

    for i, is_sp in enumerate(is_speech):
        if is_sp:
            speech_count += 1
            silence_count = 0
            if not in_speech and speech_count >= min_speech_frames // 2:
                in_speech = True
                speech_start = max(0, (i - speech_count) * hop_size / sample_rate)
        else:
            silence_count += 1
            speech_count = 0
            if in_speech and silence_count >= min_silence_frames:
                speech_end = (i - silence_count + 1) * hop_size / sample_rate
                if speech_end - speech_start >= min_speech_duration_ms / 1000:
                    speech_segments.append({"start": speech_start, "end": speech_end})
                in_speech = False

    # 마지막 구간 처리
    if in_speech:
        speech_end = len(waveform) / sample_rate
        if speech_end - speech_start >= min_speech_duration_ms / 1000:
            speech_segments.append({"start": speech_start, "end": speech_end})

    # 세그먼트가 없으면 전체 오디오를 하나의 세그먼트로
    if not speech_segments:
        speech_segments = [{"start": 0.0, "end": len(waveform) / sample_rate}]

    logger.info(f"[VAD] Detected {len(speech_segments)} speech segments")
    return speech_segments


def merge_speech_segments(
    segments: list[dict[str, float]],
    max_duration: float = 30.0,
    max_gap: float = 0.5,
) -> list[dict[str, float]]:
    """
    인접한 음성 구간을 병합하고, 긴 구간은 분할합니다.

    Args:
        segments: 음성 구간 리스트
        max_duration: 최대 구간 길이 (초)
        max_gap: 병합할 최대 갭 (초)

    Returns:
        병합/분할된 구간 리스트
    """
    if not segments:
        return []

    # 1. 인접 구간 병합 (Gap이 짧으면 하나로 합침)
    merged = []
    if segments:
        current = segments[0].copy()
        for seg in segments[1:]:
            gap = seg["start"] - current["end"]
            if gap <= max_gap:
                current["end"] = seg["end"]
            else:
                merged.append(current)
                current = seg.copy()
        merged.append(current)

    # 2. Smart Splitting (30초 제한을 지키되, 세그먼트 경계에서 자름)
    result = []
    chunk_start = merged[0]["start"]
    chunk_end = merged[0]["end"]
    
    # 누적된 현재 청크의 구성요소들 (나중에 합쳐서 하나의 청크로 만듦)
    current_chunk_segments = [merged[0]]
    
    for seg in merged[1:]:
        # 만약 이 세그먼트를 현재 청크에 포함시켰을 때 30초를 넘는지 확인
        potential_end = seg["end"]
        potential_duration = potential_end - chunk_start
        
        if potential_duration <= max_duration:
            # 30초 안쪽이면 포함 (Merge)
            chunk_end = potential_end
            current_chunk_segments.append(seg)
        else:
            # 30초를 넘기게 되면, 이전까지 모은 것들로 청크 마감 (Split at silence)
            result.append({"start": chunk_start, "end": chunk_end})
            
            # 새로운 청크 시작
            chunk_start = seg["start"]
            chunk_end = seg["end"]
            current_chunk_segments = [seg]
            
            # 만약 단일 세그먼트 자체가 이미 30초보다 크다면? (Fallback: 강제 분할)
            if (chunk_end - chunk_start) > max_duration:
                # 이 경우는 어쩔 수 없이 잘라야 함 (단어 중간에 잘릴 위험 있음)
                # 하지만 VAD가 제대로 되었다면 정말 30초 동안 쉬지 않고 말하는 경우는 드묾
                logger.warning(f"[VAD] Single segment exceeds {max_duration}s ({chunk_end - chunk_start:.2f}s). Force splitting.")
                
                start = chunk_start
                while start < chunk_end:
                    end = min(start + max_duration, chunk_end)
                    result.append({"start": start, "end": end})
                    start = end
                
                # 다음 루프를 위해 변수 리셋 (이미 처리했으므로)
                current_chunk_segments = []
                # 주의: 마지막 조각이 다음 청크의 시작점이 될 수도 있지만, 
                # 여기서는 복잡도를 낮추기 위해 그냥 강제 분할로 처리하고 마감.
                # 다음 세그먼트부터 새로 시작.
                if merged.index(seg) < len(merged) - 1:
                    next_seg = merged[merged.index(seg) + 1]
                    chunk_start = next_seg["start"]
                    chunk_end = next_seg["end"]
                    current_chunk_segments = [next_seg]
                else:
                    chunk_start = -1 # 루프 종료 신호

    # 마지막 남은 청크 처리
    if current_chunk_segments and chunk_start != -1:
        # 단일 세그먼트가 너무 큰 경우 (위 루프에서 처리 안 된 마지막 덩어리 체크)
        if (chunk_end - chunk_start) > max_duration:
             start = chunk_start
             while start < chunk_end:
                end = min(start + max_duration, chunk_end)
                result.append({"start": start, "end": end})
                start = end
        else:
            result.append({"start": chunk_start, "end": chunk_end})

    logger.info(f"[VAD] After merge/split: {len(result)} chunks (max {max_duration}s)")
    return result


def extract_audio_chunk(
    waveform: np.ndarray,
    sample_rate: int,
    start_time: float,
    end_time: float,
) -> np.ndarray:
    """
    오디오에서 특정 구간을 추출합니다.

    Args:
        waveform: 전체 오디오 웨이브폼
        sample_rate: 샘플레이트
        start_time: 시작 시간 (초)
        end_time: 끝 시간 (초)

    Returns:
        추출된 오디오 청크
    """
    start_sample = int(start_time * sample_rate)
    end_sample = int(end_time * sample_rate)
    return waveform[start_sample:end_sample]
