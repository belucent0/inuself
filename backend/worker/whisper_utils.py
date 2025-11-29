"""Whisper.cpp 기반 ASR 유틸리티."""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import librosa
import soundfile as sf

from .config import get_whisper_cli_path, get_whispercpp_model_path


def parse_whispercpp_json(json_data: dict[str, Any]) -> dict[str, Any]:
    """whisper.cpp JSON 출력을 OpenAI Whisper 형식으로 변환."""
    result = {
        "text": "",
        "language": json_data.get("result", {}).get("language", "ko"),
        "segments": []
    }
    
    transcription = json_data.get("transcription", [])
    all_texts = []
    
    for i, seg in enumerate(transcription):
        # 타임스탬프 변환: "00:00:06,000" -> 6.0 (초)
        from_ts = seg.get("timestamps", {}).get("from", "00:00:00,000")
        to_ts = seg.get("timestamps", {}).get("to", "00:00:00,000")
        
        def parse_timestamp(ts_str: str) -> float:
            """00:00:06,000 형식을 초 단위로 변환."""
            parts = ts_str.split(",")
            time_part = parts[0]  # "00:00:06"
            h, m, s = map(int, time_part.split(":"))
            seconds = h * 3600 + m * 60 + s
            if len(parts) > 1:
                milliseconds = int(parts[1])
                seconds += milliseconds / 1000.0
            return seconds
        
        start = parse_timestamp(from_ts)
        end = parse_timestamp(to_ts)
        text = seg.get("text", "").strip()
        
        all_texts.append(text)
        
        result["segments"].append({
            "id": i,
            "start": start,
            "end": end,
            "text": text,
        })
    
    result["text"] = " ".join(all_texts)
    return result


def run_asr_transcription(
    audio_path: str | Path,
    model_size: str,
    project_root: Path | None = None,
    part_label: str | None = None,
    time_range: tuple[float, float] | None = None,
    prompt: str | None = None,
) -> tuple[dict[str, Any], float, float]:
    """
    ASR 전사 실행 (동기 처리).
    
    Args:
        audio_path: 오디오 파일 경로
        model_size: Whisper 모델 크기
        project_root: 프로젝트 루트 경로
        part_label: 파트 식별자 (예: "Part 1", "Part 2")
        time_range: 시간 구간 튜플 (start, end) 또는 None
        prompt: 이전 문맥 텍스트 (--prompt 옵션으로 전달)
    
    Returns:
        (asr_result, model_load_time, transcribe_time)
    """
    part_prefix = f"[ASR {part_label}]" if part_label else "[ASR]"
    
    if time_range:
        time_info = f" [{time_range[0]:.2f}s - {time_range[1]:.2f}s]"
    else:
        time_info = ""
    
    print(f"{part_prefix} Using whisper-cli.exe with model: {model_size}{time_info}")
    
    # 모델 경로 찾기
    model_load_start = time.time()
    try:
        model_path = get_whispercpp_model_path(model_size, project_root)
        print(f"{part_prefix} Model found: {model_path}")
    except (ValueError, FileNotFoundError) as e:
        print(f"{part_prefix} Error: {e}")
        raise
    
    model_load_time = time.time() - model_load_start
    
    # whisper-cli.exe 경로
    whisper_cli = get_whisper_cli_path()
    
    if time_range:
        print(f"{part_prefix} Starting transcription for time range {time_range[0]:.2f}s - {time_range[1]:.2f}s...")
    else:
        print(f"{part_prefix} Starting transcription...")
    
    # whisper.cpp는 WAV 파일만 읽을 수 있으므로, MP4 등 다른 형식은 WAV로 변환
    audio_path_obj = Path(audio_path)
    temp_wav_path = None
    use_temp_wav = False
    
    if audio_path_obj.suffix.lower() not in ['.wav', '.wave']:
        # WAV가 아니면 임시 WAV 파일로 변환
        print(f"{part_prefix} Converting audio to WAV format (whisper.cpp requires WAV)...")
        temp_wav_path = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
        temp_wav_path.close()
        
        # librosa로 오디오 로드 후 WAV로 저장
        try:
            waveform, sample_rate = librosa.load(str(audio_path), sr=16000)
            sf.write(temp_wav_path.name, waveform, sample_rate)
            use_temp_wav = True
            actual_audio_path = temp_wav_path.name
            print(f"{part_prefix} Audio converted to WAV: {actual_audio_path}")
        except Exception as e:
            # 변환 실패 시 원본 파일 사용 (whisper.cpp가 읽을 수 있을 수도 있음)
            print(f"{part_prefix} Warning: Failed to convert to WAV ({e}), using original file")
            actual_audio_path = str(audio_path)
            if temp_wav_path and os.path.exists(temp_wav_path.name):
                try:
                    os.unlink(temp_wav_path.name)
                except:
                    pass
    else:
        actual_audio_path = str(audio_path)
    
    # 임시 JSON 출력 파일 생성
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp_file:
        json_output_path = tmp_file.name
    
    transcribe_start = time.time()
    
    # whisper-cli.exe 실행 (동기 - subprocess.run)
    # 파일명은 이미 업로드 시점에 안전한 이름으로 변경되어 있음
    # GPU 가속 사용 (기본적으로 활성화되어 있지만 명시적으로 확인)
    # --no-gpu 플래그가 있으므로 기본적으로 GPU가 활성화되어 있음
    # Vulkan 디바이스가 감지되면 자동으로 GPU 사용
    cmd = [
        whisper_cli,
        "-m", model_path,
        "-l", "ko",
        "--output-json-full",
        "--output-file", json_output_path.replace('.json', ''),
    ]
    
    # 프롬프트 옵션 추가 (문맥 주입)
    if prompt:
        cmd.extend(["--prompt", prompt])
        print(f"{part_prefix} Using prompt context: {prompt[:100]}..." if len(prompt) > 100 else f"{part_prefix} Using prompt context: {prompt}")
    
    cmd.append(actual_audio_path)
    
    # GPU 사용 확인을 위한 환경 변수 설정 (필요한 경우)
    env = os.environ.copy()
    # Vulkan 디바이스 선택 (기본값: 0)
    env.setdefault("GGML_VULKAN_DEVICE", "0")
    
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        # 출력 로그 (stdout과 stderr 모두 확인)
        if result.stderr:
            for line in result.stderr.strip().split('\n'):
                if line.strip():
                    # GPU 관련 메시지 확인
                    if 'vulkan' in line.lower() or 'gpu' in line.lower():
                        print(f"{part_prefix} [GPU] {line}")
                    else:
                        print(f"{part_prefix} {line}")
        
        if result.stdout:
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    print(f"{part_prefix} {line}")
        if result.stderr:
            for line in result.stderr.strip().split('\n'):
                if line.strip():
                    print(f"{part_prefix} {line}")
        
        if result.returncode != 0:
            raise RuntimeError(f"whisper-cli.exe failed: {result.stderr}")
        
        transcribe_time = time.time() - transcribe_start
        
        # JSON 파일 읽기
        json_file = json_output_path.replace('.json', '') + '.json'
        if not os.path.exists(json_file):
            raise FileNotFoundError(f"JSON output file not found: {json_file}")
        
        # 인코딩 문제 해결: 바이너리 모드로 읽어서 UTF-8로 디코딩
        try:
            with open(json_file, 'rb') as f:
                content = f.read()
            # BOM 제거 및 UTF-8 디코딩
            if content.startswith(b'\xef\xbb\xbf'):
                content = content[3:]
            json_data = json.loads(content.decode('utf-8', errors='replace'))
        except Exception as e:
            # 폴백: 여러 인코딩 시도
            encodings = ['utf-8-sig', 'utf-8', 'latin-1', 'cp1252']
            json_data = None
            for encoding in encodings:
                try:
                    with open(json_file, 'r', encoding=encoding, errors='replace') as f:
                        json_data = json.load(f)
                    break
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
            
            if json_data is None:
                raise RuntimeError(f"Failed to read JSON file: {json_file}, error: {e}")
        
        # OpenAI Whisper 형식으로 변환
        asr_result = parse_whispercpp_json(json_data)
        
        # 임시 파일 삭제
        try:
            os.unlink(json_file)
        except:
            pass
        
        # 임시 WAV 파일 삭제 (변환된 경우)
        if use_temp_wav and temp_wav_path and os.path.exists(temp_wav_path.name):
            try:
                os.unlink(temp_wav_path.name)
            except:
                pass
        
        if time_range:
            print(f"{part_prefix} Transcription completed in {transcribe_time:.2f} seconds (time range: {time_range[0]:.2f}s - {time_range[1]:.2f}s)")
        else:
            print(f"{part_prefix} Transcription completed in {transcribe_time:.2f} seconds")
        
        return asr_result, model_load_time, transcribe_time
        
    except Exception as e:
        # 임시 파일 정리
        try:
            if os.path.exists(json_output_path):
                os.unlink(json_output_path)
        except:
            pass
        # 임시 WAV 파일 삭제 (변환된 경우)
        if use_temp_wav and temp_wav_path and os.path.exists(temp_wav_path.name):
            try:
                os.unlink(temp_wav_path.name)
            except:
                pass
        raise

