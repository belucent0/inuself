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

from .config import setup_rocm_environment
from .diarization_utils import (
    build_nominal_ranges,
    find_optimal_split_points,
    merge_segments_with_speakers,
    run_diarization,
)
from .whisper_utils import run_asr_transcription
from .chunked_asr import run_chunked_asr, merge_asr_results

# distributed_lock을 import하기 위해 경로 추가
# pipeline.py는 backend/worker/에 있고, distributed_lock은 backend/app/worker/에 있음
_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

try:
    from app.worker.distributed_lock import acquire_lock
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
) -> PipelineResult:
    """Case 4: 화자분리와 ASR(전체 파일 또는 청킹) 병렬 처리."""
    print(f"\n{'='*60}")
    print("[Case 4] Parallel Processing: Diarization and ASR")
    print(f"{'='*60}")
    
    # 설정값 가져오기
    try:
        _backend_dir = Path(__file__).parent.parent
        if str(_backend_dir) not in sys.path:
            sys.path.insert(0, str(_backend_dir))
        from app.core.config import get_settings
        settings = get_settings()
        chunk_threshold_seconds = settings.asr_chunk_threshold_minutes * 60  # 분을 초로 변환
        chunk_duration_seconds = settings.asr_chunk_duration_minutes * 60
        overlap_seconds = settings.asr_chunk_overlap_seconds
    except Exception as e:
        print(f"[Case 4] Warning: Failed to load settings, using defaults: {e}")
        chunk_threshold_seconds = 1800  # 기본값: 30분
        chunk_duration_seconds = 1800  # 기본값: 30분
        overlap_seconds = 0
    
    # 오디오 길이에 따라 청킹 여부 결정
    use_chunking = audio_duration > chunk_threshold_seconds
    if use_chunking:
        print(f"[Case 4] Audio duration ({audio_duration:.2f}s) exceeds threshold ({chunk_threshold_seconds:.2f}s)")
        print(f"[Case 4] Using chunked ASR: chunk_duration={chunk_duration_seconds:.2f}s, overlap={overlap_seconds:.2f}s")
    else:
        print(f"[Case 4] Audio duration ({audio_duration:.2f}s) is within threshold ({chunk_threshold_seconds:.2f}s)")
        print(f"[Case 4] Using full file ASR")
    
    case_start = time.time()
    
    # ASR 락과 화자분리 락을 각각 획득한 후 병렬 실행
    # ASR 락과 화자분리 락을 각각 획득했으므로 여기서는 락 획득 로직을 제거함
    print(f"\n[Step 1] Running internal pipeline without extra locks (assumed acquired by caller)...")
    
    # 락 변수 더미 초기화 (finally 블록 호환성 유지)
    from contextlib import nullcontext
    asr_lock_ctx = nullcontext()
    diarization_lock_ctx = nullcontext()
    
    try:
        print(f"[Step 2] Starting Diarization and ASR simultaneously (with locks)...")
        
        # 화자분리와 ASR을 동시에 실행 (락을 획득한 상태에서)
        with ThreadPoolExecutor(max_workers=2) as executor:
            # 화자분리 작업 제출
            diarization_future = executor.submit(
                run_diarization,
                waveform=waveform,
                sample_rate=sample_rate,
                device=device,
                audio_duration=audio_duration,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
            
            # ASR 작업 제출 (청킹 여부에 따라 다름)
            if use_chunking:
                # 청킹된 ASR 실행 (순차 처리)
                def run_chunked_asr_wrapper():
                    """청킹된 ASR을 실행하고 결과를 병합하는 래퍼 함수."""
                    chunk_results = run_chunked_asr(
                        audio_path=audio_file_path,
                        model_size=model_size,
                        chunk_duration_seconds=chunk_duration_seconds,
                        overlap_seconds=overlap_seconds,
                        project_root=project_root,
                    )
                    # 청크 결과 병합
                    merged_result = merge_asr_results(chunk_results, overlap_seconds)
                    # run_asr_transcription과 동일한 형식으로 반환 (model_load_time, transcribe_time 포함)
                    # 청킹의 경우 모델 로드 시간은 첫 번째 청크에서만 발생하므로 0으로 설정
                    return merged_result, 0.0, 0.0
                
                asr_future = executor.submit(run_chunked_asr_wrapper)
            else:
                # 전체 파일 ASR 실행
                asr_future = executor.submit(
                    run_asr_transcription,
                    audio_path=audio_file_path,
                    model_size=model_size,
                    project_root=project_root,
                )
            
            # 두 작업 모두 완료 대기
            print(f"[Parallel] Waiting for both Diarization and ASR to complete...")
            diarization, diarization_load_time, diarization_time, embeddings_dict, pipeline = diarization_future.result()
            asr_result, model_load_time, transcribe_time = asr_future.result()
        
        # 락은 컨텍스트 매니저가 자동으로 해제
        print(f"[Step 3] Locks released after completion")
    finally:
        # diarization 락 해제
        try:
            diarization_lock_ctx.__exit__(None, None, None)
        except Exception as e:
            print(f"[Pipeline] Error releasing diarization lock: {e}")
        # ASR 락 해제
        try:
            asr_lock_ctx.__exit__(None, None, None)
        except Exception as e:
            print(f"[Pipeline] Error releasing ASR lock: {e}")
    
    execution_time = time.time() - case_start
    
    print(f"\n[Case 4] All tasks completed in {execution_time:.2f} seconds")
    print(f"  - Diarization (load + process): {diarization_load_time + diarization_time:.2f}s")
    if use_chunking:
        print(f"  - ASR (chunked): completed")
    else:
        print(f"  - ASR (load + transcribe): {model_load_time + transcribe_time:.2f}s")
    
    # 화자 정보 병합
    print(f"\n[Merging] Combining ASR and diarization results...")
    merged_segments = merge_segments_with_speakers(
        asr_result.get("segments", []),
        diarization,
    )
    
    # 화자별 통계
    speaker_stats = {}
    for seg in merged_segments:
        speaker = seg.get("speaker", "UNKNOWN")
        if speaker not in speaker_stats:
            speaker_stats[speaker] = {"count": 0, "duration": 0.0}
        speaker_stats[speaker]["count"] += 1
        speaker_stats[speaker]["duration"] += seg["end"] - seg["start"]
    
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
        "asr_chunked": use_chunking,
        "speaker_stats": speaker_stats,
    })
    
    return PipelineResult(
        transcription=transcription,
        segments=merged_segments,
        speaker_stats=speaker_stats,
        diarization_segments=diarization_segments,
        duration_seconds=audio_duration,
        logs=logs,
    )

