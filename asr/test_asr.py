# asr/test_asr.py
import whisper
import torch
import time
import sys
import os

# 로그 파일 저장을 위한 Tee 클래스
class Tee:
    """출력을 콘솔과 파일에 동시에 쓰는 클래스 (타임스탬프 포함)"""
    def __init__(self, file_path):
        from datetime import datetime
        self.file = open(file_path, 'w', encoding='utf-8')
        self.stdout = sys.stdout
        sys.stdout = self
        self.buffer = ""  # 줄 단위로 처리하기 위한 버퍼
        self.datetime = datetime
    
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
                timestamp = self.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.file.write(f"[{timestamp}] {line}\n")
            else:
                self.file.write('\n')
        self.file.flush()
    
    def flush(self):
        # 버퍼에 남은 내용 처리
        if self.buffer:
            timestamp = self.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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

# 프로젝트 루트 경로 추가 (media/wav 폴더 접근용)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Windows console encoding configuration
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

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

print(f"\n{'='*60}")
print(f"Loading Whisper model: {model_size}")
print(f"{'='*60}")

# 모델 저장 경로 설정 (프로젝트 내 관리)
whisper_dir = os.path.dirname(os.path.abspath(__file__))
model_dir = os.path.join(whisper_dir, "models")
os.makedirs(model_dir, exist_ok=True)

# 모델 로드 (프로젝트 내 models 폴더 사용)
model_load_start = time.time()
model = whisper.load_model(model_size, download_root=model_dir).to(device)
model_load_time = time.time() - model_load_start

if device == "cuda":
    gpu_memory = torch.cuda.memory_allocated(0) / 1024**3
    print(f"GPU memory used: {gpu_memory:.2f} GB")
print(f"Model loaded in {model_load_time:.2f} seconds")
print(f"Model location: {model_dir}")

# 오디오 파일 선택 (프로젝트 루트 기준)
audio_file = sys.argv[2] if len(sys.argv) > 2 else os.path.join(project_root, "media", "wav", "sample.wav")
audio_file = os.path.abspath(audio_file)

if not os.path.exists(audio_file):
    print(f"Error: Audio file '{audio_file}' not found.")
    sys.exit(1)

# 로그 및 출력 파일 경로 설정
from datetime import datetime
from pathlib import Path
import json

logs_dir = os.path.join(whisper_dir, "logs")
os.makedirs(logs_dir, exist_ok=True)

timestamp = datetime.now().strftime("%y%m%d_%H%M%S")  # YYMMDD_HHMMSS 형식
audio_name = Path(audio_file).stem
log_file = os.path.join(logs_dir, f"{timestamp}_asr_{model_size}_{audio_name}.log")
output_file = os.path.join(logs_dir, f"{timestamp}_asr_{model_size}_{audio_name}.json")

# 로그 파일 시작 (모든 출력이 파일에도 저장됨)
tee = Tee(log_file)

print(f"\n{'='*60}")
print(f"Transcribing: {audio_file}")
print(f"{'='*60}")

# 전사 수행
print("Starting transcription...")
transcribe_start = time.time()
result = model.transcribe(audio_file, language="ko")
transcribe_time = time.time() - transcribe_start
print("Transcription completed!")

# 오디오 길이 계산
import librosa

waveform, sample_rate = librosa.load(audio_file, sr=16000)
audio_duration = len(waveform) / sample_rate

# 결과 출력
print(f"\n{'='*60}")
print("Results")
print(f"{'='*60}")
print(f"Model: {model_size}")
print(f"Audio duration: {audio_duration:.2f} seconds")
print(f"Processing time: {transcribe_time:.2f} seconds")
print(f"Speed: {audio_duration/transcribe_time:.2f}x real-time")
print(f"\nTranscribed text:")
print("-" * 60)
print(result["text"])
print("-" * 60)

# 세그먼트별 출력
if "segments" in result:
    print(f"\nSegments ({len(result['segments'])}):")
    for i, seg in enumerate(result["segments"], 1):
        print(f"[{i}] [{seg['start']:.2f}s - {seg['end']:.2f}s] {seg['text']}")

# JSON 결과 파일은 이미 위에서 설정됨

output_data = {
    "model": model_size,
    "audio_file": audio_file,
    "audio_duration": audio_duration,
    "processing_time": transcribe_time,
    "speed_x": audio_duration / transcribe_time,
    "model_load_time": model_load_time,
    "gpu_memory_gb": gpu_memory if device == "cuda" else 0,
    "device": device,
    "timestamp": datetime.now().isoformat(),
    "result": {
        "text": result["text"],
        "language": result.get("language", "ko"),
        "segments": [
            {
                "id": seg.get("id", i),
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
                "no_speech_prob": seg.get("no_speech_prob"),
                "compression_ratio": seg.get("compression_ratio"),
                "avg_logprob": seg.get("avg_logprob"),
            }
            for i, seg in enumerate(result.get("segments", []))
        ]
    }
}

with open(output_file, "w", encoding="utf-8") as f:
    json.dump(output_data, f, ensure_ascii=False, indent=2)

# 완료 신호 출력 (파싱하기 쉬운 형식)
print(f"\n{'='*60}")
print("✅ TRANSCRIPTION COMPLETED")
print(f"{'='*60}")
print(f"Model: {model_size}")
print(f"Audio: {Path(audio_file).name}")
print(f"Duration: {audio_duration:.2f}s")
print(f"Processing time: {transcribe_time:.2f}s")
print(f"Speed: {audio_duration/transcribe_time:.2f}x real-time")
print(f"Results saved to: {output_file}")
print(f"Log file saved to: {log_file}")
print(f"{'='*60}")
print("✅ END OF TRANSCRIPTION")
print(f"{'='*60}")

# 로그 파일 닫기
tee.close()

