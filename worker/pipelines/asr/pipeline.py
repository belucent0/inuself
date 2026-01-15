"""ASR + 화자분리 메인 파이프라인.

Architecture V6: Worker → LiteLLM Proxy → Audio Gateway
LiteLLM이 Prometheus + GPU 세마포어로 최적 Provider를 자동 선택합니다.
GPU/ROCm 의존성 없음 - 모든 AI 추론은 Host의 GPU 서버에서 실행.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import librosa
import soundfile as sf
import tempfile

from .diarization_utils import merge_segments_with_speakers

# Architecture V6: Worker → LiteLLM → Audio Gateway
# LiteLLM이 Prometheus + GPU 세마포어로 최적 Provider 선택
from .litellm_audio_client import (
    call_litellm_transcription,
    call_litellm_diarization,
    ASRProvider,
    DiarizationAnnotationWrapper,
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
    on_asr_resource_acquired: callable = None,  # ASR 리소스 획득 후 호출할 콜백
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
    # Architecture V6: GPU/ROCm 설정 불필요 - LiteLLM이 자동 라우팅
    audio_file_path = Path(audio_file_path)
    logs = []
    
    # 오디오 로드 및 WAV 변환 (whisper.cpp는 WAV 형식 필요)
    print(f"[Pipeline] Loading audio file...")
    waveform, sample_rate = librosa.load(str(audio_file_path), sr=16000)
    audio_duration = len(waveform) / sample_rate
    print(f"[Pipeline] Audio loaded: {audio_duration:.2f} seconds")

    # WAV 파일로 변환 (임시 파일)
    wav_temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_path = Path(wav_temp_file.name)
    sf.write(wav_path, waveform, sample_rate)
    wav_temp_file.close()
    print(f"[Pipeline] Converted to WAV: {wav_path}")

    logs.append({
        "event": "audio_loaded",
        "duration": audio_duration,
        "sample_rate": sample_rate,
    })

    try:
        # Case 4: 화자분리와 ASR(전체 파일) 병렬 처리
        if processing_mode == "case4":
            return _run_case4_parallel_full_asr(
                audio_duration=audio_duration,
                audio_file_path=wav_path,  # WAV 파일 경로 전달
                logs=logs,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                accuracy_mode=accuracy_mode,
                on_asr_resource_acquired=on_asr_resource_acquired,
            )
        else:
            raise ValueError(f"Unsupported processing mode: {processing_mode}")
    finally:
        # 임시 WAV 파일 정리
        if wav_path.exists():
            wav_path.unlink()
            print(f"[Pipeline] Cleaned up temp WAV file")


def _run_case4_parallel_full_asr(
    audio_duration: float,
    audio_file_path: Path,
    logs: list[dict[str, Any]],
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    accuracy_mode: str = "speed",  # "speed" (FLM/NPU) or "accuracy" (whisper.cpp/GPU)
    on_asr_resource_acquired: callable = None,  # ASR 리소스 획득 후 호출할 콜백
) -> PipelineResult:
    """
    Case 4: 화자분리와 ASR 병렬 처리.

    Architecture V6: Worker → LiteLLM Proxy → Audio Gateway
    - LiteLLM이 Prometheus + GPU 세마포어로 최적 Provider 선택
    - ASR: LiteLLM → whisper.cpp/insanely-fast/FLM 자동 라우팅
    - Diarization: LiteLLM → Diarization Server 라우팅
    """
    print(f"\n{'='*60}")
    print(f"[Case 4] LiteLLM-based Processing (Architecture V6)")
    print(f"[Case 4] accuracy_mode={accuracy_mode}")
    print(f"{'='*60}")

    case_start = time.time()

    # ============================================================
    # Architecture V6: LiteLLM Proxy 호출
    # - LiteLLM이 Prometheus + GPU 세마포어로 Provider 선택
    # - GPU 바쁨 → 자동 Fallback (NPU 또는 대기)
    # ============================================================
    print(f"\n[Step 1] Starting LiteLLM-based parallel processing...")
    print(f"  - ASR: LiteLLM Proxy (Prometheus routing, accuracy_mode={accuracy_mode})")
    print(f"  - Diarization: LiteLLM Proxy → Diarization Server")

    # 화자분리 실패 또는 0 세그먼트 시 사용할 fallback 플래그
    diarization_fallback_used = False

    with ThreadPoolExecutor(max_workers=2) as executor:
        # 화자분리 API 호출 (LiteLLM → Diarization Server)
        diarization_future = executor.submit(
            call_litellm_diarization,
            audio_file_path=audio_file_path,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            return_embeddings=False,
        )

        # ASR API 호출 (LiteLLM 라우팅)
        # accuracy_mode에 따라 LiteLLM이 NPU(FLM) 또는 GPU(Whisper) 선택
        asr_future = executor.submit(
            call_litellm_transcription,
            audio_file_path=audio_file_path,
            accuracy_mode=accuracy_mode,
            language="ko",
            on_resource_acquired=on_asr_resource_acquired,
        )


        # 두 작업 모두 완료 대기
        print(f"[Parallel] Waiting for both API calls to complete...")

        # Diarization 결과 처리 (실패 시 무시하고 진행)
        try:
            diarization, diarization_load_time, diarization_time, embeddings_dict, pipeline, diarization_params = diarization_future.result()

            # 세그먼트 수 확인 - 0개면 fallback 사용
            diarization_segment_count = len(list(diarization.itertracks(yield_label=True)))
            if diarization_segment_count == 0:
                print(f"[Pipeline] Warning: Diarization returned 0 segments. Using fallback: SPEAKER_00")
                diarization_fallback_used = True
        except Exception as e:
            print(f"[Pipeline] Warning: Diarization API failed: {e}")
            print(f"[Pipeline] Proceeding with ASR only (fallback: SPEAKER_00)")
            diarization = DiarizationAnnotationWrapper([]) # 빈 결과
            diarization_load_time = 0.0
            diarization_time = 0.0
            embeddings_dict = {}
            pipeline = None
            diarization_params = {}
            diarization_fallback_used = True

        # ASR 결과는 필수 (에러 시 전파)
        asr_result, model_load_time, transcribe_time, asr_provider = asr_future.result()

    print(f"[Step 2] API calls completed (Provider: {asr_provider.value})")

    execution_time = time.time() - case_start

    # ASR 엔진 결정 (Provider 기반)
    asr_engine = asr_provider.value

    print(f"\n[Case 4] All tasks completed in {execution_time:.2f} seconds")
    print(f"  - Diarization: {diarization_load_time + diarization_time:.2f}s")
    print(f"  - ASR ({asr_engine}): {model_load_time + transcribe_time:.2f}s")

    # API 응답에서 세그먼트 추출
    asr_segments = asr_result.get("segments", [])
    print(f"[Pipeline] ASR returned {len(asr_segments)} segments (provider: {asr_provider.value})")

    # 모든 Provider가 세그먼트를 반환하도록 구현됨 (FLM은 VAD 청킹으로 생성)
    # 예외적으로 세그먼트가 없으면 단일 세그먼트로 fallback
    if not asr_segments and asr_result.get("text"):
        print(f"[Pipeline] Warning: No segments from ASR. Creating single fallback segment.")
        full_text = asr_result.get("text", "").strip()
        asr_segments.append({
            "id": 0,
            "start": 0.0,
            "end": audio_duration,
            "text": full_text,
        })

    # 화자 정보 병합
    print(f"\n[Merging] Combining ASR and diarization results...")
    merged_segments = merge_segments_with_speakers(
        asr_segments,
        diarization,
    )

    # Fallback: 화자분리 실패/0 세그먼트 시 모든 ASR 세그먼트를 SPEAKER_00으로 할당
    if diarization_fallback_used:
        print(f"[Fallback] Assigning all {len(merged_segments)} segments to SPEAKER_00")
        for seg in merged_segments:
            seg["speaker"] = "SPEAKER_00"
    
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
        "asr_engine": asr_provider.value,
        "architecture": "v6_litellm_proxy",
        "speaker_stats": speaker_stats,
        "diarization_params": diarization_params,
        "diarization_fallback": diarization_fallback_used,
    })
    
    return PipelineResult(
        transcription=transcription,
        segments=merged_segments,
        speaker_stats=speaker_stats,
        diarization_segments=diarization_segments,
        duration_seconds=audio_duration,
        logs=logs,
    )

