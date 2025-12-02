"""ASR + 화자분리 메인 파이프라인."""
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# HuggingFace Hub 호환성 패치를 먼저 적용 (pyannote.audio import 전에)
try:
    import huggingface_hub
    import functools
    
    # 원본 함수 백업 (이미 패치된 경우를 대비)
    if not hasattr(huggingface_hub, '_original_hf_hub_download'):
        if hasattr(huggingface_hub, 'hf_hub_download'):
            huggingface_hub._original_hf_hub_download = huggingface_hub.hf_hub_download
            
            # use_auth_token을 token으로 변환하는 래퍼
            @functools.wraps(huggingface_hub._original_hf_hub_download)
            def _patched_hf_hub_download(*args, **kwargs):
                if "use_auth_token" in kwargs:
                    kwargs["token"] = kwargs.pop("use_auth_token")
                return huggingface_hub._original_hf_hub_download(*args, **kwargs)
            
            # monkey patch 적용
            huggingface_hub.hf_hub_download = _patched_hf_hub_download
            
            # utils 모듈에도 패치 적용
            if hasattr(huggingface_hub, 'utils'):
                if hasattr(huggingface_hub.utils, 'hf_hub_download'):
                    huggingface_hub.utils.hf_hub_download = _patched_hf_hub_download
except (ImportError, AttributeError):
    # huggingface_hub가 없거나 이미 다른 방식으로 import된 경우 무시
    pass

import librosa
import soundfile as sf

# ROCm 가상환경(site-packages)을 우선적으로 사용하도록 sys.path 조정
from . import rocm_env as _rocm_env  # noqa: F401  # side-effect import

import torch

from .config import setup_rocm_environment
from .diarization_utils import (
    build_nominal_ranges,
    find_optimal_split_points,
    merge_segments_with_speakers,
    run_diarization,
    extract_speaker_embeddings,
    extract_segment_embeddings,
)
from .whisper_utils import run_asr_transcription

# Settings import를 위한 경로 조정
_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

try:
    from app.core.config import get_settings
except ImportError:
    # fallback: 직접 경로 구성
    _app_dir = _backend_dir / "app"
    if str(_app_dir) not in sys.path:
        sys.path.insert(0, str(_app_dir))
    from core.config import get_settings

# 청킹 모듈 import
from .chunked_asr import merge_asr_results, run_chunked_asr


@dataclass
class PipelineResult:
    """파이프라인 실행 결과."""
    transcription: dict[str, Any]
    segments: list[dict[str, Any]]
    speaker_stats: dict[str, Any]
    diarization_segments: list[dict[str, Any]]
    duration_seconds: float
    logs: list[dict[str, Any]] = field(default_factory=list)
    speaker_embeddings: dict[str, list[float]] | None = None  # 화자별 embedding 벡터
    segment_embeddings: list[dict[str, Any]] | None = None  # 시간대별 세그먼트 embedding 벡터
    num_speakers: int = 0  # pyannote가 구분한 화자 수


def run_asr_diarization_pipeline(
    audio_file_path: str | Path,
    model_size: str = "large-v3",
    processing_mode: str = "case4",
    num_asr_chunks: int = 2,
    project_root: Path | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> PipelineResult:
    """
    ASR + 화자분리 파이프라인 실행.
    
    Args:
        audio_file_path: 오디오 파일 경로
        model_size: Whisper 모델 크기
        processing_mode: 처리 모드 ("case1", "case2", "case3", "case4")
        num_asr_chunks: ASR 병렬 조각 수
        project_root: 프로젝트 루트 경로 (모델 경로 찾기용)
        min_speakers: 최소 화자 수 (선택사항)
        max_speakers: 최대 화자 수 (선택사항)
    
    Returns:
        PipelineResult
    """
    # ROCm 환경 설정
    setup_rocm_environment()
    
    # GPU 설정
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        gpu_name = torch.cuda.get_device_name(0)
        print(f"[Pipeline] GPU: {gpu_name}")
        torch.cuda.empty_cache()
    else:
        print("[Pipeline] GPU not available, using CPU")
    
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
    
    # Settings 로드하여 청킹 설정 확인
    try:
        settings = get_settings()
        chunk_threshold_minutes = settings.asr_chunk_threshold_minutes
        chunk_duration_minutes = settings.asr_chunk_duration_minutes
        chunk_overlap_seconds = settings.asr_chunk_overlap_seconds
    except Exception as e:
        print(f"[Pipeline] Warning: Failed to load settings, using defaults: {e}")
        chunk_threshold_minutes = 60
        chunk_duration_minutes = 30
        chunk_overlap_seconds = 30
    
    # 청킹 여부 결정
    audio_duration_minutes = audio_duration / 60.0
    use_chunking = audio_duration_minutes >= chunk_threshold_minutes
    
    if use_chunking:
        print(f"[Pipeline] Audio duration ({audio_duration_minutes:.2f} min) >= threshold ({chunk_threshold_minutes} min)")
        print(f"[Pipeline] Using chunked ASR processing (chunk size: {chunk_duration_minutes} min, overlap: {chunk_overlap_seconds} s)")
        logs.append({
            "event": "chunking_enabled",
            "chunk_duration_minutes": chunk_duration_minutes,
            "chunk_overlap_seconds": chunk_overlap_seconds,
        })
    else:
        print(f"[Pipeline] Audio duration ({audio_duration_minutes:.2f} min) < threshold ({chunk_threshold_minutes} min)")
        print(f"[Pipeline] Using standard ASR processing (no chunking)")
    
    # Case 4: 화자분리와 ASR(전체 파일 또는 청킹) 병렬 처리
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
            use_chunking=use_chunking,
            chunk_duration_seconds=chunk_duration_minutes * 60,
            chunk_overlap_seconds=chunk_overlap_seconds,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
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
    use_chunking: bool = False,
    chunk_duration_seconds: float = 1800.0,  # 30분 기본값
    chunk_overlap_seconds: float = 30.0,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> PipelineResult:
    """Case 4: 화자분리와 ASR(전체 파일 또는 청킹) 병렬 처리."""
    print(f"\n{'='*60}")
    if use_chunking:
        print("[Case 4] Parallel Processing: Diarization and ASR (Chunked)")
    else:
        print("[Case 4] Parallel Processing: Diarization and ASR (Full File)")
    print(f"{'='*60}")
    
    case_start = time.time()
    
    # 화자분리와 ASR을 동시에 실행
    print(f"\n[Step 1] Starting Diarization and ASR simultaneously...")
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        # 화자분리 작업 제출 (임베딩 및 pipeline 포함)
        diarization_future = executor.submit(
            run_diarization,
            waveform=waveform,
            sample_rate=sample_rate,
            device=device,
            audio_duration=audio_duration,
            return_embeddings=True,  # 임베딩 추출 활성화
            return_pipeline=True,  # pipeline 객체도 반환 (시간대별 임베딩 추출용)
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        
        # ASR 작업 제출 (전체 파일 또는 청킹)
        if use_chunking:
            asr_future = executor.submit(
                run_chunked_asr,
                audio_path=audio_file_path,
                model_size=model_size,
                chunk_duration_seconds=chunk_duration_seconds,
                overlap_seconds=chunk_overlap_seconds,
                project_root=project_root,
            )
        else:
            asr_future = executor.submit(
                run_asr_transcription,
                audio_path=audio_file_path,
                model_size=model_size,
                project_root=project_root,
            )
        
        # 화자분리 작업 완료 대기 (타임아웃: 30분)
        print(f"[Parallel] Waiting for Diarization to complete...")
        try:
            # 화자 분리 작업에 타임아웃 설정 (30분)
            diarization_timeout = 30 * 60  # 30분
            diarization_result = diarization_future.result(timeout=diarization_timeout)
        except TimeoutError:
            print(f"[Parallel] ERROR: Diarization task timed out after {diarization_timeout/60:.1f} minutes")
            raise RuntimeError(f"Diarization task timed out after {diarization_timeout/60:.1f} minutes. The task may be stuck.")
        # return_embeddings=True, return_pipeline=True로 변경했으므로 결과 구조 확인
        if isinstance(diarization_result, tuple) and len(diarization_result) == 5:
            diarization, diarization_load_time, diarization_time, embeddings_dict, diarization_pipeline = diarization_result
        elif isinstance(diarization_result, tuple) and len(diarization_result) == 4:
            diarization, diarization_load_time, diarization_time, embeddings_dict = diarization_result
            diarization_pipeline = None
        else:
            # 기존 호환성 유지
            diarization, diarization_load_time, diarization_time = diarization_result
            embeddings_dict = None
            diarization_pipeline = None
        
        print(f"[Parallel] Diarization completed, extracting segment embeddings before ASR completion...")
        
        # 화자분리 완료 후 즉시 세그먼트 임베딩 추출 (ASR 완료 전에)
        segment_embeddings = None
        if diarization_pipeline is not None:
            print(f"[Embeddings] Extracting time-based segment embeddings...")
            audio_data_for_embeddings = {
                "waveform": torch.from_numpy(waveform).unsqueeze(0).to(device),
                "sample_rate": sample_rate
            }
            # 세그먼트 임베딩 추출 (세그먼트 수가 너무 많으면 건너뛰기)
            try:
                # 세그먼트가 너무 많으면 (1000개 이상) 임베딩 추출 건너뛰기
                # 각 세그먼트마다 GPU 연산이 필요하므로 시간이 오래 걸릴 수 있음
                segment_count = len(list(diarization.itertracks(yield_label=True)))
                if segment_count > 1000:
                    print(f"[Embeddings] Warning: Too many segments ({segment_count}), skipping segment embeddings extraction to avoid timeout")
                    print(f"[Embeddings] Consider using speaker embeddings instead for large files")
                    segment_embeddings = None
                else:
                    print(f"[Embeddings] Extracting embeddings for {segment_count} segments...")
                    segment_embeddings = extract_segment_embeddings(
                        diarization_pipeline,
                        audio_data_for_embeddings,
                        diarization,
                        min_segment_duration=0.5,  # 최소 0.5초 세그먼트만 추출
                    )
            except Exception as e:
                print(f"[Embeddings] ERROR: Segment embeddings extraction failed: {e}")
                import traceback
                traceback.print_exc()
                segment_embeddings = None
            
            # 세그먼트 임베딩 추출 완료 후 pipeline 해제 및 VRAM 정리
            print(f"[Embeddings] Releasing diarization pipeline and freeing VRAM...")
            del diarization_pipeline
            diarization_pipeline = None
            if device == "cuda":
                torch.cuda.empty_cache()
                print(f"[Embeddings] VRAM freed")
            
            if segment_embeddings:
                print(f"[Embeddings] Successfully extracted {len(segment_embeddings)} segment embeddings")
            else:
                print(f"[Embeddings] No segment embeddings extracted (returned None or empty list)")
        else:
            print(f"[Embeddings] Cannot extract segment embeddings: diarization_pipeline is None")
            segment_embeddings = None
        
        # 이제 ASR 완료 대기
        print(f"[Parallel] Waiting for ASR to complete...")
        if use_chunking:
            # 청킹 결과는 리스트로 반환됨
            chunk_results = asr_future.result()
            # 청킹 결과 병합
            asr_result = merge_asr_results(chunk_results, chunk_overlap_seconds)
            # 청킹 모드에서는 모델 로드 시간과 전사 시간을 추정
            model_load_time = 0.0  # 청킹 모드에서는 각 청크마다 로드되므로 정확한 시간 측정 어려움
            transcribe_time = time.time() - case_start - diarization_load_time - diarization_time
        else:
            asr_result, model_load_time, transcribe_time = asr_future.result()
    
    execution_time = time.time() - case_start
    
    print(f"\n[Case 4] All tasks completed in {execution_time:.2f} seconds")
    print(f"  - Diarization (load + process): {diarization_load_time + diarization_time:.2f}s")
    if use_chunking:
        print(f"  - ASR (chunked, total): {transcribe_time:.2f}s")
    else:
        print(f"  - ASR (load + transcribe): {model_load_time + transcribe_time:.2f}s")
    
    # 화자 정보 병합
    print(f"\n[Merging] Combining ASR and diarization results...")
    print(f"[Merging] Splitting overlapping speech segments...")
    merged_segments = merge_segments_with_speakers(
        asr_result.get("segments", []),
        diarization,
        embeddings_dict=embeddings_dict,
        split_overlaps=True,  # 겹치는 발화 구간을 별도 세그먼트로 분리
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
    
    # pyannote가 구분한 화자 수 계산
    unique_speakers = set()
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        unique_speakers.add(speaker)
    num_speakers = len(unique_speakers)
    
    # 임베딩이 없으면 수동 추출 시도
    if embeddings_dict is None:
        print(f"[Pipeline] Embeddings not returned, attempting manual extraction...")
        try:
            # pipeline 객체를 다시 로드해야 하므로, 여기서는 None으로 유지
            # 대신 로그에 기록
            print(f"[Pipeline] Warning: Embeddings extraction skipped (requires pipeline object)")
        except Exception as e:
            print(f"[Pipeline] Warning: Failed to extract embeddings: {e}")
    
    # 임베딩 정보 로깅
    if embeddings_dict:
        print(f"\n[Pipeline] Speaker embeddings extracted:")
        print(f"  - Number of speakers with embeddings: {len(embeddings_dict)}")
        for speaker, embedding in embeddings_dict.items():
            embedding_dim = len(embedding) if isinstance(embedding, list) else 0
            print(f"  - {speaker}: embedding dimension = {embedding_dim}")
            # 처음 5개 값만 표시
            if isinstance(embedding, list) and len(embedding) > 0:
                preview = embedding[:5]
                print(f"    Preview: {preview}...")
    else:
        print(f"[Pipeline] No embeddings extracted")
    
    print(f"\n[Pipeline] Diarization summary:")
    print(f"  - Total speakers detected: {num_speakers}")
    print(f"  - Speaker labels: {sorted(unique_speakers)}")
    
    # 최종 transcription 구성
    transcription = {
        "text": asr_result["text"],
        "language": asr_result.get("language", "ko"),
        "segments": merged_segments,
    }
    
    # 시간대별 임베딩 요약 (로깅용)
    segment_embeddings_summary = None
    if segment_embeddings:
        # 화자별로 그룹화하여 요약
        speaker_segment_counts = {}
        for seg_emb in segment_embeddings:
            speaker = seg_emb.get("speaker", "UNKNOWN")
            if speaker not in speaker_segment_counts:
                speaker_segment_counts[speaker] = {
                    "count": 0,
                    "total_duration": 0.0,
                    "time_ranges": [],
                }
            speaker_segment_counts[speaker]["count"] += 1
            speaker_segment_counts[speaker]["total_duration"] += seg_emb.get("duration", 0.0)
            # 처음 5개 시간 범위만 저장
            if len(speaker_segment_counts[speaker]["time_ranges"]) < 5:
                speaker_segment_counts[speaker]["time_ranges"].append({
                    "start": seg_emb.get("start"),
                    "end": seg_emb.get("end"),
                    "embedding_dim": len(seg_emb.get("embedding", [])),
                })
        
        segment_embeddings_summary = speaker_segment_counts
    
    logs.append({
        "event": "completed",
        "execution_time": execution_time,
        "diarization_time": diarization_load_time + diarization_time,
        "asr_time": model_load_time + transcribe_time,
        "speaker_stats": speaker_stats,
        "num_speakers": num_speakers,
        "speaker_labels": sorted(unique_speakers),
        "embeddings_extracted": embeddings_dict is not None,
        "num_embeddings": len(embeddings_dict) if embeddings_dict else 0,
        "segment_embeddings_extracted": segment_embeddings is not None,
        "num_segment_embeddings": len(segment_embeddings) if segment_embeddings else 0,
        "segment_embeddings_summary": segment_embeddings_summary,
    })
    
    return PipelineResult(
        transcription=transcription,
        segments=merged_segments,
        speaker_stats=speaker_stats,
        diarization_segments=diarization_segments,
        duration_seconds=audio_duration,
        logs=logs,
        speaker_embeddings=embeddings_dict,
        segment_embeddings=segment_embeddings,
        num_speakers=num_speakers,
    )

