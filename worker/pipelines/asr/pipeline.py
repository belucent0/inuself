"""ASR + 화자분리 메인 파이프라인."""
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import librosa
import soundfile as sf

from .rocm_config import setup_rocm_environment
from .diarization_utils import (
    build_nominal_ranges,
    find_optimal_split_points,
    merge_segments_with_speakers,
)
# Architecture V4: Worker는 직접 추론하지 않고 API만 호출
from .audio_gateway_client import (
    call_transcription_api,
    call_diarization_api,
)

# worker 패키지에서 distributed_lock 가져오기
try:
    from worker.distributed_lock import acquire_lock
except ImportError:
    # distributed_lock을 import할 수 없는 경우 (테스트 환경 등)
    # 락 없이 진행하도록 더미 함수 제공
    from contextlib import contextmanager
    @contextmanager
    def acquire_lock(lock_key: str, timeout: float = 3600.0, blocking_timeout: float = 0.0):
        print(f"[Pipeline] Lock not available, proceeding without lock: {lock_key}")
        yield True


@dataclass
class PipelineResult:
    """파이프라인 실행 결과."""
    transcription: dict[str, Any]
    segments: list[dict[str, Any]]
    speaker_stats: dict[str, Any]
    diarization_segments: list[dict[str, Any]]
    duration_seconds: float
    logs: list[dict[str, Any]] = field(default_factory=list)


def run_asr_diarization_pipeline(
    audio_file_path: str | Path,
    model_size: str = "large-v3",
    processing_mode: str = "case4",
    num_asr_chunks: int = 2,
    project_root: Path | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    file_id: int | None = None,  # 락 회복을 위한 file_id
    accuracy_mode: str = "speed",  # "speed" (FLM/NPU) or "accuracy" (whisper.cpp/GPU)
) -> PipelineResult:
    """
    ASR + 화자분리 파이프라인 실행.
    
    Args:
        audio_file_path: 오디오 파일 경로
        model_size: Whisper 모델 크기
        processing_mode: 처리 모드 ("case1", "case2", "case3", "case4")
        num_asr_chunks: ASR 병렬 조각 수
        project_root: 프로젝트 루트 경로 (모델 경로 찾기용)
    
    Returns:
        PipelineResult
    """
    # ROCm 환경 설정 (PyTorch import 전에 실행)
    try:
        setup_rocm_environment()
    except Exception as e:
        # ROCm 설정 실패 시에도 계속 진행
        print(f"[Pipeline] Warning: ROCm setup failed: {e}, continuing...")
    
    # PyTorch import (ROCm 환경 설정 후)
    try:
        import torch
    except (OSError, ImportError, RuntimeError) as e:
        raise RuntimeError(
            f"Failed to import PyTorch: {e}\n"
            "This may be due to missing DLLs or incompatible PyTorch installation.\n"
            "Please ensure PyTorch with ROCm support is properly installed."
        ) from e
    
    # GPU 설정 (ROCm은 CUDA 호환 레이어를 제공하므로 torch.cuda.is_available() 사용)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        try:
            gpu_name = torch.cuda.get_device_name(0)
            print(f"[Pipeline] ROCm GPU detected: {gpu_name}")
            torch.cuda.empty_cache()
        except Exception as e:
            print(f"[Pipeline] Warning: ROCm GPU initialization failed: {e}, falling back to CPU")
            device = "cpu"
    else:
        print("[Pipeline] ROCm GPU not available, using CPU")
    
    audio_file_path = Path(audio_file_path)
    logs = []
    
    # 오디오 로드
    print(f"[Pipeline] Loading audio file...")
    waveform, sample_rate = librosa.load(str(audio_file_path), sr=16000)
    audio_duration = len(waveform) / sample_rate
    print(f"[Pipeline] Audio loaded: {audio_duration:.2f} seconds")
    
    logs.append({
        "event": "audio_loaded",
        "duration": audio_duration,
        "sample_rate": sample_rate,
    })
    
    # Case 4: 화자분리와 ASR(전체 파일) 병렬 처리
    if processing_mode == "case4":
        return _run_case4_parallel_full_asr(
            waveform=waveform,
            sample_rate=sample_rate,
            audio_duration=audio_duration,
            audio_file_path=audio_file_path,
            model_size=model_size,
            device=device,
            project_root=project_root,
            logs=logs,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            file_id=file_id,
            accuracy_mode=accuracy_mode,
        )
    else:
        raise ValueError(f"Unsupported processing mode: {processing_mode}")


def _run_case4_parallel_full_asr(
    waveform: Any,
    sample_rate: int,
    audio_duration: float,
    audio_file_path: Path,
    model_size: str,
    device: str,
    project_root: Path | None,
    logs: list[dict[str, Any]],
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    file_id: int | None = None,  # 락 회복을 위한 file_id
    accuracy_mode: str = "speed",  # "speed" (FLM/NPU) or "accuracy" (whisper.cpp/GPU)
) -> PipelineResult:
    """
    Case 4: 화자분리와 ASR 병렬 처리.

    Architecture V4: Worker는 직접 추론하지 않고 API만 호출.
    - ASR: LiteLLM을 통해 라우팅 (speed→NPU/FLM, accuracy→GPU/Whisper)
    - Diarization: Audio Gateway 직접 호출 (항상 GPU)
    """
    print(f"\n{'='*60}")
    print(f"[Case 4] API-based Parallel Processing (Architecture V4)")
    print(f"[Case 4] accuracy_mode={accuracy_mode}")
    print(f"{'='*60}")

    case_start = time.time()

    # ============================================================
    # Architecture V4: LiteLLM/Audio Gateway API 호출
    # - ASR: LiteLLM이 accuracy_mode에 따라 NPU/GPU 자동 라우팅
    # - Diarization: Audio Gateway 직접 호출 (GPU에서만 실행)
    # ============================================================
    print(f"\n[Step 1] Starting API-based parallel processing...")
    print(f"  - ASR: LiteLLM routing (accuracy_mode={accuracy_mode})")
    print(f"  - Diarization: Audio Gateway direct call")

    with ThreadPoolExecutor(max_workers=2) as executor:
        # 화자분리 API 호출 (Audio Gateway - GPU)
        diarization_future = executor.submit(
            call_diarization_api,
            audio_file_path=audio_file_path,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            return_embeddings=False,
        )

        # ASR API 호출 (LiteLLM 라우팅)
        # accuracy_mode에 따라 LiteLLM이 NPU(FLM) 또는 GPU(Whisper) 선택
        asr_future = executor.submit(
            call_transcription_api,
            audio_file_path=audio_file_path,
            accuracy_mode=accuracy_mode,
            language="ko",
        )

        # 두 작업 모두 완료 대기
        print(f"[Parallel] Waiting for both API calls to complete...")
        diarization, diarization_load_time, diarization_time, embeddings_dict, pipeline, diarization_params = diarization_future.result()
        asr_result, model_load_time, transcribe_time = asr_future.result()

    print(f"[Step 2] API calls completed")

    execution_time = time.time() - case_start

    # ASR 엔진 결정 (accuracy_mode 기반)
    asr_engine = "FLM/NPU" if accuracy_mode == "speed" else "Whisper/GPU"

    print(f"\n[Case 4] All tasks completed in {execution_time:.2f} seconds")
    print(f"  - Diarization (Audio Gateway): {diarization_load_time + diarization_time:.2f}s")
    print(f"  - ASR ({asr_engine}, via LiteLLM): {model_load_time + transcribe_time:.2f}s")

    # API 응답에서 세그먼트 추출
    asr_segments = asr_result.get("segments", [])
    
    # 화자 정보 병합
    print(f"\n[Merging] Combining ASR and diarization results...")
    merged_segments = merge_segments_with_speakers(
        asr_segments,
        diarization,
    )
    
    # 화자별 통계
    # 화자별 통계 (겹치는 화자 "A & B"는 분리하여 각각 집계)
    speaker_stats = {}
    for seg in merged_segments:
        speaker_label = seg.get("speaker", "UNKNOWN")
        
        # " & "로 화자 분리 (예: "SPEAKER_00 & SPEAKER_01")
        if " & " in speaker_label:
            speakers = speaker_label.split(" & ")
        else:
            speakers = [speaker_label]
            
        duration = seg["end"] - seg["start"]
        
        for speaker in speakers:
            if speaker not in speaker_stats:
                speaker_stats[speaker] = {"count": 0, "duration": 0.0}
            speaker_stats[speaker]["count"] += 1
            speaker_stats[speaker]["duration"] += duration
    
    # 화자 세그먼트 추출
    diarization_segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        diarization_segments.append({
            "start": turn.start,
            "end": turn.end,
            "speaker": speaker,
        })
    
    # 최종 transcription 구성
    transcription = {
        "text": asr_result["text"],
        "language": asr_result.get("language", "ko"),
        "segments": merged_segments,
    }
    
    logs.append({
        "event": "completed",
        "execution_time": execution_time,
        "diarization_time": diarization_load_time + diarization_time,
        "asr_time": model_load_time + transcribe_time,
        "accuracy_mode": accuracy_mode,
        "asr_engine": "flm" if accuracy_mode == "speed" else "whisper",
        "architecture": "v4_api_based",
        "speaker_stats": speaker_stats,
        "diarization_params": diarization_params,
    })
    
    return PipelineResult(
        transcription=transcription,
        segments=merged_segments,
        speaker_stats=speaker_stats,
        diarization_segments=diarization_segments,
        duration_seconds=audio_duration,
        logs=logs,
    )

