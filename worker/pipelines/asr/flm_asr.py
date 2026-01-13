"""FLM(FastFlowLM) 서버를 통한 배치 ASR 전사 유틸리티."""
import os
import time
import tempfile
import socket
from pathlib import Path
from typing import Any
import subprocess


class FLMServerUnavailableError(Exception):
    """FLM 서버가 사용 불가능할 때 발생하는 예외."""
    pass


def is_flm_server_available(timeout: float = 2.0) -> bool:
    """
    FLM 서버가 사용 가능한지 확인합니다.
    
    Args:
        timeout: 연결 타임아웃 (초)
    
    Returns:
        bool: 서버 사용 가능 여부
    """
    from urllib.parse import urlparse
    
    flm_base_url = os.getenv("FLM_BASE_URL", "http://127.0.0.1:11434")
    parsed = urlparse(flm_base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434
    
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            result = s.connect_ex((host, port))
            return result == 0
    except Exception:
        return False


def run_flm_asr_transcription(
    audio_path: Path,
    language: str = "ko",
) -> tuple[dict[str, Any], float, float]:
    """
    FLM 서버를 통해 오디오 파일을 전사합니다.
    
    Args:
        audio_path: 오디오 파일 경로
        language: 언어 코드 (ko, en, ja, zh, auto 등)
    
    Returns:
        tuple: (전사 결과 dict, 모델 로드 시간, 전사 시간)
        전사 결과 dict는 {"text": str, "segments": list, "language": str} 형태
    """
    import httpx
    
    # FLM 서버 URL 설정
    flm_base_url = os.getenv("FLM_BASE_URL", "http://127.0.0.1:11434")
    flm_url = f"{flm_base_url}/v1/audio/transcriptions"
    
    print(f"[FLM ASR] Starting transcription: {audio_path}")
    print(f"[FLM ASR] Server URL: {flm_url}")
    
    # FLM 서버 사용 가능 여부 확인
    if not is_flm_server_available():
        raise FLMServerUnavailableError(f"FLM server is not available at {flm_base_url}")
    
    start_time = time.time()
    
    # WAV 변환 (FLM 서버는 WAV 포맷을 선호)
    wav_path = _convert_to_wav(audio_path)
    
    try:
        with open(wav_path, "rb") as f:
            audio_data = f.read()
        
        # FLM 서버에 전사 요청
        files = {"file": ("audio.wav", audio_data, "audio/wav")}
        data = {
            "model": os.getenv("FLM_WHISPER_MODEL", "whisper-turbo"),
            "language": language,
            "response_format": "verbose_json",  # 세그먼트 정보 포함
        }
        
        print(f"[FLM ASR] Sending request to FLM server...")
        
        # 긴 오디오 파일을 위해 타임아웃을 충분히 설정 (30분)
        with httpx.Client(timeout=1800.0, headers={"Authorization": "Bearer flm"}) as client:
            response = client.post(flm_url, files=files, data=data)
        
        transcribe_time = time.time() - start_time
        
        if response.status_code != 200:
            raise RuntimeError(f"FLM server error: {response.status_code} - {response.text}")
        
        result = response.json()
        print(f"[FLM ASR] Transcription completed in {transcribe_time:.2f}s")
        print(f"[FLM ASR] Response keys: {result.keys()}")
        
        # FLM 서버 응답을 whisper.cpp와 호환되는 형식으로 변환
        text = result.get("text", "").strip()
        segments = result.get("segments", [])
        
        print(f"[FLM ASR] Text length: {len(text)}, Segments count: {len(segments)}")
        
        # 세그먼트 형식 변환 (FLM 형식 -> 표준 형식)
        converted_segments = []
        for i, seg in enumerate(segments):
            converted_segments.append({
                "id": i,
                "start": seg.get("start", 0.0),
                "end": seg.get("end", 0.0),
                "text": seg.get("text", "").strip(),
            })
        
        # 세그먼트가 없는 경우 전체 텍스트로 하나의 세그먼트 생성
        if not converted_segments and text:
            # 오디오 길이 추정 (파일 크기 기반)
            import librosa
            duration = librosa.get_duration(path=str(wav_path))
            converted_segments = [{
                "id": 0,
                "start": 0.0,
                "end": duration,
                "text": text,
            }]
        
        transcription_result = {
            "text": text,
            "segments": converted_segments,
            "language": language,
        }
        
        # FLM은 모델이 항상 로드되어 있으므로 모델 로드 시간은 0
        return transcription_result, 0.0, transcribe_time
        
    finally:
        # 임시 WAV 파일 삭제 (원본과 다른 경우에만)
        if wav_path != audio_path and wav_path.exists():
            try:
                wav_path.unlink()
            except Exception:
                pass


def _convert_to_wav(audio_path: Path) -> Path:
    """
    오디오 파일을 WAV 포맷으로 변환합니다.
    이미 WAV인 경우 그대로 반환합니다.
    """
    if audio_path.suffix.lower() == ".wav":
        return audio_path
    
    # ffmpeg을 사용하여 WAV로 변환
    output_path = Path(tempfile.gettempdir()) / f"flm_asr_{audio_path.stem}.wav"
    
    cmd = [
        "ffmpeg",
        "-y",  # 덮어쓰기
        "-i", str(audio_path),
        "-ar", "16000",  # 16kHz 샘플레이트
        "-ac", "1",  # 모노
        "-f", "wav",
        str(output_path),
    ]
    
    print(f"[FLM ASR] Converting to WAV: {audio_path} -> {output_path}")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed: {result.stderr}")
    
    return output_path


def run_flm_asr_with_diarization_segments(
    audio_path: Path,
    diarization_segments: list[dict[str, Any]],
    language: str = "ko",
) -> tuple[dict[str, Any], float, float]:
    """
    화자분리 세그먼트를 기반으로 FLM ASR을 수행합니다.
    각 화자분리 세그먼트에 대해 해당 오디오 청크를 추출하여 전사합니다.
    
    Args:
        audio_path: 오디오 파일 경로
        diarization_segments: 화자분리 결과 세그먼트 리스트 [{"start": float, "end": float, "speaker": str}, ...]
        language: 언어 코드
    
    Returns:
        tuple: (전사 결과 dict, 모델 로드 시간, 전사 시간)
    """
    import librosa
    import soundfile as sf
    import httpx
    
    print(f"[FLM ASR] Starting diarization-based transcription: {audio_path}")
    print(f"[FLM ASR] Diarization segments count: {len(diarization_segments)}")
    
    # FLM 서버 URL 설정
    flm_base_url = os.getenv("FLM_BASE_URL", "http://127.0.0.1:11434")
    flm_url = f"{flm_base_url}/v1/audio/transcriptions"
    
    # FLM 서버 사용 가능 여부 확인
    if not is_flm_server_available():
        raise FLMServerUnavailableError(f"FLM server is not available at {flm_base_url}")
    
    start_time = time.time()
    
    # 오디오 로드
    waveform, sample_rate = librosa.load(str(audio_path), sr=16000)
    total_duration = len(waveform) / sample_rate
    
    print(f"[FLM ASR] Audio duration: {total_duration:.2f}s")
    
    # 화자분리 세그먼트가 없으면 전체 오디오를 하나의 세그먼트로 처리
    if not diarization_segments:
        print(f"[FLM ASR] No diarization segments, using full audio")
        return run_flm_asr_transcription(audio_path, language)
    
    # 인접한 같은 화자 세그먼트 병합 (너무 짧은 세그먼트 방지)
    merged_diar_segments = _merge_adjacent_speaker_segments(diarization_segments, min_duration=1.0)
    print(f"[FLM ASR] Merged diarization segments: {len(merged_diar_segments)}")
    
    # 긴 세그먼트 분할 (30초 초과 세그먼트는 분할)
    merged_diar_segments = _split_long_segments(merged_diar_segments, max_duration=30.0)
    
    # 각 세그먼트에 대해 전사 수행
    all_segments = []
    total_text = []
    total_transcribe_time = 0.0
    segment_id = 0
    
    with httpx.Client(timeout=300.0, headers={"Authorization": "Bearer flm"}) as client:
        for i, diar_seg in enumerate(merged_diar_segments):
            seg_start = diar_seg["start"]
            seg_end = diar_seg["end"]
            speaker = diar_seg.get("speaker", "UNKNOWN")
            
            # 세그먼트가 너무 짧으면 스킵
            if seg_end - seg_start < 0.5:
                continue
            
            # 오디오 청크 추출
            start_sample = int(seg_start * sample_rate)
            end_sample = int(seg_end * sample_rate)
            chunk_waveform = waveform[start_sample:end_sample]
            
            # 임시 파일로 저장
            chunk_path = Path(tempfile.gettempdir()) / f"flm_diar_chunk_{i}.wav"
            sf.write(str(chunk_path), chunk_waveform, sample_rate)
            
            try:
                # FLM으로 전사
                with open(chunk_path, "rb") as f:
                    audio_data = f.read()
                
                files = {"file": ("audio.wav", audio_data, "audio/wav")}
                data = {
                    "model": os.getenv("FLM_WHISPER_MODEL", "whisper-turbo"),
                    "language": language,
                }
                
                seg_start_time = time.time()
                response = client.post(flm_url, files=files, data=data)
                seg_transcribe_time = time.time() - seg_start_time
                total_transcribe_time += seg_transcribe_time
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get("text", "").strip()
                    
                    if text:
                        all_segments.append({
                            "id": segment_id,
                            "start": seg_start,
                            "end": seg_end,
                            "text": text,
                            "speaker": speaker,
                        })
                        total_text.append(text)
                        segment_id += 1
                        
                        if i % 10 == 0:
                            print(f"[FLM ASR] Processed segment {i+1}/{len(merged_diar_segments)}: {seg_start:.2f}s-{seg_end:.2f}s")
                else:
                    print(f"[FLM ASR] Warning: Segment {i} failed: {response.status_code}")
                    
            finally:
                # 임시 파일 삭제
                if chunk_path.exists():
                    chunk_path.unlink()
    
    total_time = time.time() - start_time
    print(f"[FLM ASR] Diarization-based transcription completed: {len(all_segments)} segments, {total_time:.2f}s total")
    
    final_result = {
        "text": " ".join(total_text),
        "segments": all_segments,
        "language": language,
    }
    
    return final_result, 0.0, total_transcribe_time


def _merge_adjacent_speaker_segments(
    segments: list[dict[str, Any]],
    min_duration: float = 1.0,
    max_gap: float = 0.5,
) -> list[dict[str, Any]]:
    """
    인접한 같은 화자 세그먼트를 병합합니다.
    
    Args:
        segments: 화자분리 세그먼트 리스트
        min_duration: 최소 세그먼트 길이 (초)
        max_gap: 병합할 최대 갭 (초)
    
    Returns:
        병합된 세그먼트 리스트
    """
    if not segments:
        return []
    
    # 시간순 정렬
    sorted_segments = sorted(segments, key=lambda x: x["start"])
    
    merged = []
    current = sorted_segments[0].copy()
    
    for seg in sorted_segments[1:]:
        # 같은 화자이고 갭이 작으면 병합
        if (seg.get("speaker") == current.get("speaker") and 
            seg["start"] - current["end"] <= max_gap):
            current["end"] = seg["end"]
        else:
            # 최소 길이 이상인 경우에만 추가
            if current["end"] - current["start"] >= min_duration:
                merged.append(current)
            current = seg.copy()
    
    # 마지막 세그먼트 추가
    if current["end"] - current["start"] >= min_duration:
        merged.append(current)
    
    return merged


def _split_long_segments(
    segments: list[dict[str, Any]],
    max_duration: float = 30.0,
) -> list[dict[str, Any]]:
    """
    긴 세그먼트를 지정된 최대 길이로 분할합니다.
    
    Args:
        segments: 세그먼트 리스트
        max_duration: 최대 세그먼트 길이 (초)
    
    Returns:
        분할된 세그먼트 리스트
    """
    if not segments:
        return []
    
    result = []
    
    for seg in segments:
        seg_start = seg["start"]
        seg_end = seg["end"]
        speaker = seg.get("speaker", "UNKNOWN")
        duration = seg_end - seg_start
        
        if duration <= max_duration:
            # 세그먼트가 충분히 짧으면 그대로 추가
            result.append(seg.copy())
        else:
            # 긴 세그먼트를 max_duration 단위로 분할
            current_start = seg_start
            while current_start < seg_end:
                current_end = min(current_start + max_duration, seg_end)
                result.append({
                    "start": current_start,
                    "end": current_end,
                    "speaker": speaker,
                })
                current_start = current_end
    
    print(f"[FLM ASR] Split long segments: {len(segments)} -> {len(result)} segments (max {max_duration}s)")
    
    return result


def run_chunked_flm_asr(
    audio_path: Path,
    chunk_duration_seconds: float = 1800.0,  # 30분
    overlap_seconds: float = 0.0,
    language: str = "ko",
) -> tuple[dict[str, Any], float, float]:
    """
    긴 오디오 파일을 청킹하여 FLM 서버로 전사합니다.
    
    Args:
        audio_path: 오디오 파일 경로
        chunk_duration_seconds: 청크 길이 (초)
        overlap_seconds: 청크 간 오버랩 (초)
        language: 언어 코드
    
    Returns:
        tuple: (전사 결과 dict, 모델 로드 시간, 전사 시간)
    """
    import librosa
    import soundfile as sf
    
    print(f"[FLM ASR] Starting chunked transcription: {audio_path}")
    
    # 오디오 로드
    waveform, sample_rate = librosa.load(str(audio_path), sr=16000)
    total_duration = len(waveform) / sample_rate
    
    print(f"[FLM ASR] Total duration: {total_duration:.2f}s")
    
    # 청크 분할 및 처리
    chunk_results = []
    chunk_start = 0.0
    chunk_index = 0
    total_transcribe_time = 0.0
    
    while chunk_start < total_duration:
        chunk_end = min(chunk_start + chunk_duration_seconds, total_duration)
        
        # 청크 추출
        start_sample = int(chunk_start * sample_rate)
        end_sample = int(chunk_end * sample_rate)
        chunk_waveform = waveform[start_sample:end_sample]
        
        # 임시 파일로 저장
        chunk_path = Path(tempfile.gettempdir()) / f"flm_chunk_{chunk_index}.wav"
        sf.write(str(chunk_path), chunk_waveform, sample_rate)
        
        print(f"[FLM ASR] Processing chunk {chunk_index}: {chunk_start:.2f}s - {chunk_end:.2f}s")
        
        try:
            # FLM으로 전사
            result, _, transcribe_time = run_flm_asr_transcription(chunk_path, language)
            total_transcribe_time += transcribe_time
            
            # 세그먼트 타임스탬프 조정
            for seg in result.get("segments", []):
                seg["start"] += chunk_start
                seg["end"] += chunk_start
            
            chunk_results.append({
                "chunk_start": chunk_start,
                "chunk_end": chunk_end,
                "result": result,
            })
        finally:
            # 임시 파일 삭제
            if chunk_path.exists():
                chunk_path.unlink()
        
        # 다음 청크 시작점 (오버랩 적용)
        chunk_start = chunk_end - overlap_seconds
        chunk_index += 1
    
    # 청크 결과 병합
    merged_text = ""
    merged_segments = []
    
    for chunk_data in chunk_results:
        result = chunk_data["result"]
        merged_text += " " + result.get("text", "")
        merged_segments.extend(result.get("segments", []))
    
    # 세그먼트 ID 재할당
    for i, seg in enumerate(merged_segments):
        seg["id"] = i
    
    final_result = {
        "text": merged_text.strip(),
        "segments": merged_segments,
        "language": language,
    }
    
    print(f"[FLM ASR] Chunked transcription completed: {len(chunk_results)} chunks, {total_transcribe_time:.2f}s total")
    
    return final_result, 0.0, total_transcribe_time


def get_speech_timestamps_energy(
    waveform: "np.ndarray",
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
    import numpy as np
    
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
        time_sec = i * hop_size / sample_rate
        
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
    
    # 구간이 없으면 전체 오디오 반환
    if not speech_segments:
        return [{"start": 0.0, "end": len(waveform) / sample_rate}]
    
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
    
    # 1. 인접 구간 병합
    merged = []
    current = segments[0].copy()
    
    for seg in segments[1:]:
        gap = seg["start"] - current["end"]
        if gap <= max_gap:
            current["end"] = seg["end"]
        else:
            merged.append(current)
            current = seg.copy()
    merged.append(current)
    
    # 2. 긴 구간 분할
    result = []
    for seg in merged:
        duration = seg["end"] - seg["start"]
        if duration <= max_duration:
            result.append(seg)
        else:
            # 분할
            current_start = seg["start"]
            while current_start < seg["end"]:
                current_end = min(current_start + max_duration, seg["end"])
                result.append({"start": current_start, "end": current_end})
                current_start = current_end
    
    return result


def run_flm_asr_parallel_with_vad(
    audio_path: Path,
    language: str = "ko",
    max_chunk_duration: float = 30.0,
) -> tuple[dict[str, Any], float, float]:
    """
    VAD 기반 청킹 + FLM 전사.
    화자분리와 병렬로 실행하기 위해, 각 청크의 offset을 기록하여 세그먼트를 생성합니다.
    
    Args:
        audio_path: 오디오 파일 경로
        language: 언어 코드
        max_chunk_duration: 최대 청크 길이 (초)
    
    Returns:
        tuple: (전사 결과 dict, 모델 로드 시간, 전사 시간)
    """
    import numpy as np
    import librosa
    import soundfile as sf
    import httpx
    
    print(f"[FLM ASR] Starting VAD-based parallel transcription: {audio_path}")
    
    # FLM 서버 URL 설정
    flm_base_url = os.getenv("FLM_BASE_URL", "http://127.0.0.1:11434")
    flm_url = f"{flm_base_url}/v1/audio/transcriptions"
    
    # FLM 서버 사용 가능 여부 확인
    if not is_flm_server_available():
        raise FLMServerUnavailableError(f"FLM server is not available at {flm_base_url}")
    
    start_time = time.time()
    
    # 오디오 로드
    waveform, sample_rate = librosa.load(str(audio_path), sr=16000)
    total_duration = len(waveform) / sample_rate
    
    print(f"[FLM ASR] Audio duration: {total_duration:.2f}s")
    
    # VAD로 음성 구간 감지
    speech_segments = get_speech_timestamps_energy(waveform, sample_rate)
    print(f"[FLM ASR] VAD detected {len(speech_segments)} speech segments")
    
    # 구간 병합 및 분할 (30초 이하로)
    processed_segments = merge_speech_segments(speech_segments, max_duration=max_chunk_duration)
    print(f"[FLM ASR] After merge/split: {len(processed_segments)} chunks")
    
    # 각 청크별 전사
    all_segments = []
    total_text = []
    total_transcribe_time = 0.0
    segment_id = 0
    
    with httpx.Client(timeout=300.0, headers={"Authorization": "Bearer flm"}) as client:
        for i, chunk_info in enumerate(processed_segments):
            chunk_start = chunk_info["start"]
            chunk_end = chunk_info["end"]
            chunk_duration = chunk_end - chunk_start
            
            # 오디오 청크 추출
            start_sample = int(chunk_start * sample_rate)
            end_sample = int(chunk_end * sample_rate)
            chunk_waveform = waveform[start_sample:end_sample]
            
            # 임시 파일로 저장
            chunk_path = Path(tempfile.gettempdir()) / f"flm_vad_chunk_{i}.wav"
            sf.write(str(chunk_path), chunk_waveform, sample_rate)
            
            try:
                # FLM으로 전사
                with open(chunk_path, "rb") as f:
                    audio_data = f.read()
                
                files = {"file": ("audio.wav", audio_data, "audio/wav")}
                data = {
                    "model": os.getenv("FLM_WHISPER_MODEL", "whisper-turbo"),
                    "language": language,
                }
                
                seg_start_time = time.time()
                response = client.post(flm_url, files=files, data=data)
                seg_transcribe_time = time.time() - seg_start_time
                total_transcribe_time += seg_transcribe_time
                
                if response.status_code == 200:
                    result = response.json()
                    text = result.get("text", "").strip()
                    
                    if text:
                        # 청크의 시작 시간(offset)을 기록한 세그먼트 생성
                        all_segments.append({
                            "id": segment_id,
                            "start": chunk_start,
                            "end": chunk_end,
                            "text": text,
                        })
                        total_text.append(text)
                        segment_id += 1
                        
                        if i % 5 == 0 or i == len(processed_segments) - 1:
                            print(f"[FLM ASR] Processed chunk {i+1}/{len(processed_segments)}: {chunk_start:.2f}s-{chunk_end:.2f}s ({seg_transcribe_time:.2f}s)")
                else:
                    print(f"[FLM ASR] Warning: Chunk {i} failed: {response.status_code}")
                    
            finally:
                # 임시 파일 삭제
                if chunk_path.exists():
                    chunk_path.unlink()
    
    total_time = time.time() - start_time
    print(f"[FLM ASR] VAD-based transcription completed: {len(all_segments)} segments, {total_time:.2f}s total")
    
    final_result = {
        "text": " ".join(total_text),
        "segments": all_segments,
        "language": language,
    }
    
    return final_result, 0.0, total_transcribe_time
