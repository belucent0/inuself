"""ASR + 화자분리 메인 파이프라인.

Architecture V7.0: Worker → Redis Stream → Provider Manager (Host)
- 병렬 처리: ASR + 화자분리 동시 실행 (Redis Stream으로 Docker Desktop 크래시 해결)
- Provider Manager가 GPU/NPU 서버 직접 호출 (localhost)
- GPU/ROCm 의존성 없음 - 모든 AI 추론은 Host의 GPU 서버에서 실행
"""
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# OpenTelemetry context 전파 (ThreadPoolExecutor용)
from opentelemetry import context as otel_context

from .diarization_utils import merge_segments_with_speakers, merge_by_diarization_segments

# Architecture V6: Worker → AI Gateway → Audio Gateway
# AI Gateway가 Prometheus + GPU 세마포어로 최적 Provider 선택
from .ai_gateway_audio_client import (
    call_ai_gateway_transcription,
    call_ai_gateway_diarization,
    ASRProvider,
    DiarizationAnnotationWrapper,
    acquire_gpu_lock,
    release_gpu_lock,
    start_lock_heartbeat,
    stop_lock_heartbeat,
    LOCK_TTL_ASR,
)


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
    on_progress: Callable[[float, str], None] | None = None,
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
    # Architecture V6: GPU/ROCm 설정 불필요 - AI Gateway가 자동 라우팅
    audio_file_path = Path(audio_file_path)
    logs = []
    
    # 오디오 로드 및 WAV 변환 (whisper.cpp는 WAV 형식 필요)
    print(f"[Pipeline] Loading audio file...")

    # 1. ffprobe로 duration 추출
    result = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_file_path)
    ], capture_output=True, text=True, check=True)
    audio_duration = float(result.stdout.strip())
    sample_rate = 16000  # 고정값 (ffmpeg -ar 16000)
    print(f"[Pipeline] Audio loaded: {audio_duration:.2f} seconds")
    if on_progress:
        on_progress(15, "오디오 분석 완료")

    # 2. ffmpeg로 WAV 변환 (16kHz, Mono, 16-bit PCM)
    wav_temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_path = Path(wav_temp_file.name)
    wav_temp_file.close()

    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-i", str(audio_file_path),
        "-ar", "16000",  # 16kHz 리샘플링
        "-ac", "1",      # Mono
        "-c:a", "pcm_s16le",  # 16-bit PCM
        str(wav_path)
    ], check=True)
    print(f"[Pipeline] Converted to WAV: {wav_path}")
    if on_progress:
        on_progress(20, "오디오 변환 완료")

    logs.append({
        "event": "audio_loaded",
        "duration": audio_duration,
        "sample_rate": sample_rate,
    })

    try:
        # V7.0: ASR + 화자분리 병렬 처리 (Redis Stream으로 Docker Desktop 크래시 해결)
        if processing_mode == "case4":
            return _run_case4_parallel_processing(
                audio_duration=audio_duration,
                audio_file_path=wav_path,  # WAV 파일 경로 전달
                logs=logs,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                file_id=file_id,
                accuracy_mode=accuracy_mode,
                on_progress=on_progress,
            )
        else:
            raise ValueError(f"Unsupported processing mode: {processing_mode}")
    finally:
        # 임시 WAV 파일 정리
        if wav_path.exists():
            wav_path.unlink()
            print(f"[Pipeline] Cleaned up temp WAV file")


def _run_case4_parallel_processing(
    audio_duration: float,
    audio_file_path: Path,
    logs: list[dict[str, Any]],
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    file_id: int | None = None,
    accuracy_mode: str = "speed",  # "speed" (whisper.cpp/GPU) or "accuracy" (insanely-fast/GPU)
    on_progress: Callable[[float, str], None] | None = None,
) -> PipelineResult:
    """
    V7.0: ASR + 화자분리 병렬 처리.

    Architecture V7.0: Worker → Redis Stream → Provider Manager (Host)
    - 병렬 처리로 처리 시간 단축
    - Redis Stream으로 Docker Desktop 크래시 해결 (host.docker.internal HTTP 제거)
    - Provider Manager가 GPU 서버에 localhost로 직접 접근
    """
    print(f"\n{'='*60}")
    print(f"[Pipeline] Parallel Processing (Architecture V7.0)")
    print(f"[Pipeline] accuracy_mode={accuracy_mode}")
    print(f"[Pipeline] Running: ASR + Diarization simultaneously")
    print(f"{'='*60}")

    case_start = time.time()

    # 화자분리 실패 또는 0 세그먼트 시 사용할 fallback 플래그
    diarization_fallback_used = False

    # 결과 저장용 변수
    asr_result = None
    asr_provider = None
    model_load_time = 0.0
    transcribe_time = 0.0
    diarization = None
    diarization_load_time = 0.0
    diarization_time = 0.0
    diarization_params = {}

    # ============================================================
    # 병렬 실행: ASR + Diarization 동시 실행
    # OpenTelemetry context를 명시적으로 복원하여 스레드에 전파
    # V7.5: Worker측에서 GPU 잠금 획득 후 ASR+Diarization 공유
    # ============================================================

    # 현재 OpenTelemetry context 저장 (ThreadPoolExecutor로 전달)
    current_otel_context = otel_context.get_current()

    # V7.5: ASR+Diarization 묶음 잠금 획득 (Worker측에서 한 번만)
    # 두 작업이 동일한 lock_id를 공유하여 AI Gateway에서 재획득 스킵
    print(f"\n[Parallel] Acquiring GPU lock for ASR+Diarization bundle...")
    lock_id = acquire_gpu_lock(timeout=LOCK_TTL_ASR, max_wait=3600.0)
    if lock_id:
        print(f"[Parallel] GPU lock acquired: {lock_id[:8]}...")
    else:
        print(f"[Parallel] Warning: GPU lock failed, proceeding without lock")
    if on_progress:
        on_progress(25, "GPU 확보")

    heartbeat = None
    if lock_id:
        heartbeat = start_lock_heartbeat(lock_id, LOCK_TTL_ASR)

    def run_asr():
        """ASR 작업 실행 (OpenTelemetry context 복원)."""
        # OpenTelemetry context 복원
        token = otel_context.attach(current_otel_context)
        try:
            return call_ai_gateway_transcription(
                audio_file_path=audio_file_path,
                accuracy_mode=accuracy_mode,
                language="ko",
                lock_id=lock_id,  # V7.5: Worker측 잠금 ID 전달
                file_id=str(file_id) if file_id else None,  # Backend 상태 업데이트용
            )
        finally:
            otel_context.detach(token)

    def run_diarization():
        """Diarization 작업 실행 (OpenTelemetry context 복원)."""
        # OpenTelemetry context 복원
        token = otel_context.attach(current_otel_context)
        try:
            return call_ai_gateway_diarization(
                audio_file_path=audio_file_path,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
                return_embeddings=False,
                lock_id=lock_id,  # V7.5: Worker측 잠금 ID 전달
            )
        finally:
            otel_context.detach(token)

    print(f"\n[Parallel] Starting ASR and Diarization simultaneously...")
    if on_progress:
        on_progress(28, "음성 인식 + 화자분리 처리 중")

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            # 두 작업을 동시에 시작 (OpenTelemetry context가 각 스레드에서 복원됨)
            asr_future = executor.submit(run_asr)
            diarization_future = executor.submit(run_diarization)

            # as_completed로 개별 완료 감지하여 progress 발행
            for future in as_completed([asr_future, diarization_future]):
                if future is asr_future:
                    try:
                        asr_result, model_load_time, transcribe_time, asr_provider = future.result()
                        print(f"[Parallel] ASR completed: {len(asr_result.get('segments', []))} segments")
                        print(f"[Parallel] ASR Provider: {asr_provider.value}")
                        if on_progress:
                            on_progress(55, "음성 인식 완료")
                    except Exception as e:
                        print(f"[Pipeline] Error: ASR failed: {e}")
                        raise
                elif future is diarization_future:
                    try:
                        diarization, diarization_load_time, diarization_time, embeddings_dict, pipeline, diarization_params = future.result()
                        diarization_segment_count = len(list(diarization.itertracks(yield_label=True)))
                        if diarization_segment_count == 0:
                            print(f"[Pipeline] Warning: Diarization returned 0 segments. Using fallback: SPEAKER_00")
                            diarization_fallback_used = True
                        else:
                            print(f"[Parallel] Diarization completed: {diarization_segment_count} segments")
                        if on_progress:
                            on_progress(65, "화자분리 완료")
                    except Exception as e:
                        print(f"[Pipeline] Warning: Diarization failed: {e}")
                        print(f"[Pipeline] Proceeding with ASR only (fallback: SPEAKER_00)")
                        diarization = DiarizationAnnotationWrapper([])
                        diarization_load_time = 0.0
                        diarization_time = 0.0
                        diarization_params = {}
                        diarization_fallback_used = True
    finally:
        # Heartbeat 중지 후 잠금 해제
        stop_lock_heartbeat(heartbeat)
        if lock_id:
            released = release_gpu_lock(lock_id)
            if released:
                print(f"[Parallel] GPU lock released: {lock_id[:8]}...")
            else:
                print(f"[Parallel] Warning: GPU lock release failed")

    execution_time = time.time() - case_start

    # ASR 엔진 결정 (Provider 기반)
    asr_engine = asr_provider.value

    print(f"\n[Pipeline] Parallel processing completed in {execution_time:.2f}s")
    print(f"  - ASR ({asr_engine}): {model_load_time + transcribe_time:.2f}s")
    print(f"  - Diarization: {diarization_load_time + diarization_time:.2f}s")

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

    # 화자 정보 병합 (V1: ASR 세그먼트 기준)
    print(f"\n[Merging] Combining ASR and diarization results...")
    if on_progress:
        on_progress(72, "결과 병합 중")
    merged_segments = merge_segments_with_speakers(
        asr_segments,
        diarization,
    )

    # ============================================================
    # Whisper 환각(Hallucination) 필터링
    # - 화자분리에서 음성이 없다고 판단한 구간 (UNKNOWN + overlap_ratio=0)
    # - 오디오 길이를 초과하는 세그먼트
    # - 화자분리 마지막 end를 초과하는 세그먼트 (V1.1 추가)
    # ============================================================

    # 화자분리의 마지막 음성 종료 시점 계산
    last_diarization_end = 0.0
    for turn, _, _ in diarization.itertracks(yield_label=True):
        if turn.end > last_diarization_end:
            last_diarization_end = turn.end
    print(f"[Hallucination] Last diarization end: {last_diarization_end:.2f}s, Audio duration: {audio_duration:.2f}s")

    pre_filter_count = len(merged_segments)
    filtered_segments = []
    hallucination_count = 0

    for seg in merged_segments:
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", 0)

        # 1. 오디오 길이 초과 세그먼트 필터링 (1초 여유)
        if seg_end > audio_duration + 1.0:
            hallucination_count += 1
            print(f"[Hallucination] Filtered (beyond audio): {seg_start:.2f}s ~ {seg_end:.2f}s")
            continue

        # 2. 화자분리 마지막 end를 초과하는 세그먼트 필터링 (V1.1 추가)
        #    - ASR 세그먼트 시작이 화자분리 범위를 완전히 벗어난 경우
        if seg_start > last_diarization_end + 1.0:
            hallucination_count += 1
            print(f"[Hallucination] Filtered (beyond diarization): {seg_start:.2f}s ~ {seg_end:.2f}s - '{seg.get('text', '')[:50]}...'")
            continue

        # 3. 화자분리에서 음성 없음 + overlap_ratio=0 → 환각으로 간주
        if seg.get("speaker") == "UNKNOWN" and seg.get("overlap_ratio", 0) == 0:
            hallucination_count += 1
            print(f"[Hallucination] Filtered (no speaker match): {seg_start:.2f}s ~ {seg_end:.2f}s - '{seg.get('text', '')[:50]}...'")
            continue

        filtered_segments.append(seg)

    if hallucination_count > 0:
        print(f"[Hallucination] Filtered {hallucination_count} segments ({pre_filter_count} → {len(filtered_segments)})")

    merged_segments = filtered_segments
    if on_progress:
        on_progress(78, "후처리 완료")

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
    
    # 최종 transcription 구성 (환각 필터링 후 텍스트 재빌드)
    final_text = " ".join(
        seg.get("text", "").strip()
        for seg in merged_segments
        if seg.get("text", "").strip()
    )
    transcription = {
        "text": final_text,
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
        "architecture": "v7.0_parallel",
        "merge_logic": "v1.1_asr_based",  # V1.1: ASR 기준 + 화자분리 범위 필터링
        "processing_mode": "parallel",
        "speaker_stats": speaker_stats,
        "diarization_params": diarization_params,
        "diarization_fallback": diarization_fallback_used,
        "hallucination_filtered": hallucination_count,
    })
    
    return PipelineResult(
        transcription=transcription,
        segments=merged_segments,
        speaker_stats=speaker_stats,
        diarization_segments=diarization_segments,
        duration_seconds=audio_duration,
        logs=logs,
    )

