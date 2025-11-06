# whisper/test_asr_with_diarization.py
"""
ASR(전사) + 화자 분리 통합 스크립트
"""
import whisper
import torch
import time
import sys
import os
import librosa
import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# 로그 파일 저장을 위한 Tee 클래스
class Tee:
    """출력을 콘솔과 파일에 동시에 쓰는 클래스 (타임스탬프 포함)"""
    def __init__(self, file_path):
        self.file = open(file_path, 'w', encoding='utf-8')
        self.stdout = sys.stdout
        sys.stdout = self
        self.buffer = ""  # 줄 단위로 처리하기 위한 버퍼
    
    def write(self, data):
        # 콘솔에는 원본 출력
        self.stdout.write(data)
        self.stdout.flush()
        
        # 파일에는 타임스탬프 추가
        self.buffer += data
        # 줄바꿈이 있으면 처리
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            if line.strip():  # 빈 줄이 아니면 타임스탬프 추가
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.file.write(f"[{timestamp}] {line}\n")
            else:
                self.file.write('\n')
        self.file.flush()
    
    def flush(self):
        # 버퍼에 남은 내용 처리
        if self.buffer:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.file.write(f"[{timestamp}] {self.buffer}")
            self.buffer = ""
        self.file.flush()
        self.stdout.flush()
    
    def close(self):
        # 버퍼 비우기
        self.flush()
        sys.stdout = self.stdout
        self.file.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

# 프로젝트 루트 경로 추가
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Windows console encoding configuration
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 결과 저장 경로 설정 (로그 파일 시작 전에 미리 설정)
whisper_dir = Path(os.path.dirname(os.path.abspath(__file__)))
output_dir = whisper_dir / "logs"
output_dir.mkdir(exist_ok=True)

# MIOpen optimization settings (from test_pyannote.py)
MIOPEN_MODE = 8  # FAST mode - 최적 설정

if MIOPEN_MODE == 8:
    print("[Info] Mode 8: FAST mode - Limited algorithm search")
    import rocm_sdk_devel
    import rocm_sdk_core
    devel_path = os.path.dirname(rocm_sdk_devel.__file__)
    core_path = os.path.dirname(rocm_sdk_core.__file__)
    site_lib_path = os.path.dirname(devel_path)
    
    possible_include_paths = [
        os.path.join(site_lib_path, '_rocm_sdk_devel', '_rocm_sdk_devel', 'include'),
        os.path.join(site_lib_path, '_rocm_sdk_core', 'include'),
        os.path.join(core_path, '..', '_rocm_sdk_core', 'include'),
        os.path.join(devel_path, 'include'),
        os.path.join(devel_path, 'rocm', 'include'),
        os.path.join(devel_path, '..', 'rocm_sdk_libraries', 'include'),
    ]
    include_paths = []
    for path in possible_include_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            include_paths.append(abs_path)
    
    if include_paths:
        include_path_str = ';'.join(include_paths)
        os.environ['HIP_INCLUDE_PATH'] = include_path_str
        os.environ['HIP_PLATFORM'] = 'amd'
        os.environ['ROCM_PATH'] = os.path.dirname(include_paths[0])
        os.environ['CPLUS_INCLUDE_PATH'] = include_path_str
        os.environ['C_INCLUDE_PATH'] = include_path_str
        hip_include_flags = ' '.join([f'-I{path}' for path in include_paths])
        os.environ['HIPCC_COMPILE_FLAGS_APPEND'] = hip_include_flags
    
    os.environ['MIOPEN_FIND_MODE'] = 'FAST'
    os.environ['MIOPEN_DISABLE_CACHE'] = '1'
    os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'
    torch.backends.cudnn.benchmark = True
    USE_MIOPEN_OPTIMIZATION = True
else:
    USE_MIOPEN_OPTIMIZATION = False
    torch.backends.cudnn.enabled = False

# GPU 설정
device = "cuda" if torch.cuda.is_available() else "cpu"
if device == "cuda":
    gpu_name = torch.cuda.get_device_name(0)
    print(f"GPU: {gpu_name}")
    torch.cuda.empty_cache()
else:
    print("GPU not available, using CPU")

# 모델 선택 (명령줄 인자 또는 기본값)
model_size = sys.argv[1] if len(sys.argv) > 1 else "base"
if model_size not in whisper.available_models():
    print(f"Error: Model '{model_size}' not available.")
    print(f"Available models: {whisper.available_models()}")
    sys.exit(1)

# 오디오 파일 선택
audio_file = sys.argv[2] if len(sys.argv) > 2 else os.path.join(project_root, "wavs", "sample.wav")
audio_file = os.path.abspath(audio_file)

if not os.path.exists(audio_file):
    print(f"Error: Audio file '{audio_file}' not found.")
    sys.exit(1)

# 로그 파일 시작 (모든 출력이 파일에도 저장됨)
timestamp = datetime.now().strftime("%y%m%d_%H%M%S")  # YYMMDD_HHMMSS 형식
audio_name = Path(audio_file).stem
log_file = output_dir / f"{timestamp}_asr_diarization_{model_size}_{audio_name}.log"
tee = Tee(log_file)

print(f"\n{'='*60}")
print("ASR + Speaker Diarization Pipeline")
print(f"{'='*60}")
print(f"Whisper model: {model_size}")
print(f"Audio file: {Path(audio_file).name}")
print(f"{'='*60}")

total_start_time = time.time()

# 오디오 로드 (두 작업 모두 필요)
print(f"\n[Loading] Loading audio file...")
waveform, sample_rate = librosa.load(audio_file, sr=16000)
audio_duration = len(waveform) / sample_rate
print(f"Audio loaded: {audio_duration:.2f} seconds")

# ==========================================
# 병렬 처리: ASR과 화자 분리를 동시에 실행
# ==========================================
print(f"\n[Parallel Processing] Starting ASR and Diarization in parallel...")

def get_whispercpp_model_path(model_size):
    """whisper.cpp 모델 경로 찾기"""
    # 모델명 매핑
    model_mapping = {
        "tiny": "ggml-tiny.bin",
        "base": "ggml-base.bin",
        "small": "ggml-small.bin",
        "medium": "ggml-medium.bin",
        "large": "ggml-large.bin",
        "large-v1": "ggml-large-v1.bin",
        "large-v2": "ggml-large-v2.bin",
        "large-v3": "ggml-large-v3.bin",
        "large-v3-turbo": "ggml-large-v3-turbo.bin",
        "turbo": "ggml-large-v3-turbo.bin",
    }
    
    model_filename = model_mapping.get(model_size)
    if not model_filename:
        raise ValueError(f"Unsupported model size: {model_size}")
    
    # 우선순위 1: 프로젝트 내 모델
    whisper_dir = os.path.dirname(os.path.abspath(__file__))
    project_model_path = os.path.join(whisper_dir, "models", model_filename)
    if os.path.exists(project_model_path):
        return project_model_path
    
    # 우선순위 2: C:/whisper-cpp/models/
    external_model_path = os.path.join("C:/whisper-cpp/models", model_filename)
    if os.path.exists(external_model_path):
        return external_model_path
    
    raise FileNotFoundError(f"Model file not found: {model_filename}")

def parse_whispercpp_json(json_data):
    """whisper.cpp JSON 출력을 OpenAI Whisper 형식으로 변환"""
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
        
        def parse_timestamp(ts_str):
            """00:00:06,000 형식을 초 단위로 변환"""
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

def run_asr():
    """ASR 전사 실행 (whisper-cli.exe 사용)"""
    print(f"[ASR] Using whisper-cli.exe with model: {model_size}")
    
    # 모델 경로 찾기
    model_load_start = time.time()
    try:
        model_path = get_whispercpp_model_path(model_size)
        print(f"[ASR] Model found: {model_path}")
    except (ValueError, FileNotFoundError) as e:
        print(f"[ASR] Error: {e}")
        raise
    
    model_load_time = time.time() - model_load_start
    
    # whisper-cli.exe 경로
    whisper_cli = "C:/whisper-cpp/build/bin/Release/whisper-cli.exe"
    if not os.path.exists(whisper_cli):
        raise FileNotFoundError(f"whisper-cli.exe not found: {whisper_cli}")
    
    print(f"[ASR] Starting transcription...")
    
    # 임시 JSON 출력 파일 생성
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, dir=os.path.dirname(os.path.abspath(__file__))) as tmp_file:
        json_output_path = tmp_file.name
    
    transcribe_start = time.time()
    
    try:
        # whisper-cli.exe 실행
        cmd = [
            whisper_cli,
            "-m", model_path,
            "-l", "ko",
            "--output-json-full",
            "--output-file", json_output_path.replace('.json', ''),
            audio_file
        ]
        
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        # whisper-cli.exe의 출력을 로그에 기록
        if process.stdout:
            for line in process.stdout.strip().split('\n'):
                if line.strip():
                    print(f"[ASR] {line}")
        if process.stderr:
            for line in process.stderr.strip().split('\n'):
                if line.strip():
                    print(f"[ASR] {line}")
        
        if process.returncode != 0:
            raise RuntimeError(f"whisper-cli.exe failed: {process.stderr}")
        
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
        result = parse_whispercpp_json(json_data)
        
        # 임시 파일 삭제
        try:
            os.unlink(json_file)
        except:
            pass
            
    except Exception as e:
        # 임시 파일 정리
        try:
            if os.path.exists(json_output_path):
                os.unlink(json_output_path)
        except:
            pass
        raise
    
    transcribe_time = time.time() - transcribe_start
    
    print(f"[ASR] Transcription completed in {transcribe_time:.2f} seconds")
    return result, model_load_time, transcribe_time

def run_diarization():
    """화자 분리 실행"""
    print(f"[Diarization] Loading speaker diarization model...")
    from pyannote.audio import Pipeline
    
    diarization_load_start = time.time()
    diarization_pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
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

# 병렬 실행
parallel_start = time.time()
with ThreadPoolExecutor(max_workers=2) as executor:
    asr_future = executor.submit(run_asr)
    diarization_future = executor.submit(run_diarization)
    
    # 결과 대기
    asr_result, model_load_time, transcribe_time = asr_future.result()
    diarization, diarization_load_time, diarization_time = diarization_future.result()

parallel_time = time.time() - parallel_start
print(f"\n[Parallel Processing] Both tasks completed in {parallel_time:.2f} seconds")
print(f"  - ASR: {model_load_time + transcribe_time:.2f}s")
print(f"  - Diarization: {diarization_load_time + diarization_time:.2f}s")
print(f"  - Time saved: {(model_load_time + transcribe_time + diarization_load_time + diarization_time) - parallel_time:.2f}s")

# ==========================================
# 3. 결과 병합 (ASR 세그먼트에 화자 정보 추가)
# ==========================================
print(f"\n[Merging] Combining ASR and diarization results...")

# 화자 세그먼트를 딕셔너리로 변환 (빠른 조회)
speaker_segments = {}
for turn, _, speaker in diarization.itertracks(yield_label=True):
    speaker_segments[(turn.start, turn.end)] = speaker

# 각 ASR 세그먼트에 가장 가까운 화자 할당
merged_segments = []
for seg in asr_result.get("segments", []):
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

# 병합 시간 계산
merge_start = time.time()
merge_time = time.time() - merge_start
total_time = time.time() - total_start_time

# ==========================================
# 4. 결과 출력 및 저장
# ==========================================
print(f"\n{'='*60}")
print("Results")
print(f"{'='*60}")
print(f"Audio duration: {audio_duration:.2f} seconds")
print(f"Total processing time: {total_time:.2f} seconds")
print(f"  - Parallel execution: {parallel_time:.2f}s")
print(f"    * ASR (load + transcribe): {model_load_time + transcribe_time:.2f}s")
print(f"    * Diarization (load + process): {diarization_load_time + diarization_time:.2f}s")
print(f"  - Result merging: {merge_time:.2f}s")
print(f"Speed: {audio_duration/total_time:.2f}x real-time")

# 화자별 통계
speaker_stats = {}
for seg in merged_segments:
    speaker = seg.get("speaker", "UNKNOWN")
    if speaker not in speaker_stats:
        speaker_stats[speaker] = {"count": 0, "duration": 0.0}
    speaker_stats[speaker]["count"] += 1
    speaker_stats[speaker]["duration"] += seg["end"] - seg["start"]

print(f"\nSpeaker statistics:")
for speaker, stats in sorted(speaker_stats.items()):
    print(f"  {speaker}: {stats['count']} segments, {stats['duration']:.2f}s")

print(f"\nTranscribed text with speakers:")
print("-" * 60)
for seg in merged_segments:
    speaker = seg.get("speaker", "UNKNOWN")
    print(f"[{speaker}] [{seg['start']:.2f}s-{seg['end']:.2f}s] {seg['text']}")
print("-" * 60)

# JSON 결과 파일 경로
output_file = output_dir / f"{timestamp}_asr_diarization_{model_size}_{audio_name}.json"

output_data = {
    "model": model_size,
    "audio_file": audio_file,
    "audio_duration": audio_duration,
    "total_processing_time": total_time,
    "parallel_execution_time": parallel_time,
    "asr_load_time": model_load_time,
    "asr_time": transcribe_time,
    "diarization_load_time": diarization_load_time,
    "diarization_time": diarization_time,
    "merge_time": merge_time,
    "speed_x": audio_duration / total_time,
    "gpu_memory_gb": torch.cuda.memory_allocated(0) / 1024**3 if device == "cuda" else 0,
    "device": device,
    "timestamp": datetime.now().isoformat(),
    "speaker_stats": {k: v for k, v in speaker_stats.items()},
    "result": {
        "text": asr_result["text"],
        "language": asr_result.get("language", "ko"),
        "segments": [
            {
                "id": seg.get("id", i),
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
                "speaker": seg.get("speaker", "UNKNOWN"),
                "no_speech_prob": seg.get("no_speech_prob"),
                "compression_ratio": seg.get("compression_ratio"),
                "avg_logprob": seg.get("avg_logprob"),
            }
            for i, seg in enumerate(merged_segments)
        ]
    }
}

with open(output_file, "w", encoding="utf-8") as f:
    json.dump(output_data, f, ensure_ascii=False, indent=2)

# 완료 신호
print(f"\n{'='*60}")
print("✅ TRANSCRIPTION + DIARIZATION COMPLETED")
print(f"{'='*60}")
print(f"Model: {model_size}")
print(f"Audio: {Path(audio_file).name}")
print(f"Duration: {audio_duration:.2f}s")
print(f"Total time: {total_time:.2f}s")
print(f"Speed: {audio_duration/total_time:.2f}x real-time")
print(f"Results saved to: {output_file}")
print(f"Log file saved to: {log_file}")
print(f"{'='*60}")
print("✅ END OF PROCESSING")
print(f"{'='*60}")

# 로그 파일 닫기
tee.close()

