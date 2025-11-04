# test_pyannote.py
import torch
import time
import os
import librosa
import sys
import threading
from diarization_logger import DiarizationLogger


# Windows console encoding configuration (prevent Unicode errors)
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# MIOpen cache directory setup and existing cache deletion (prevent SQLite errors)
cache_dir = os.path.join(os.path.expanduser('~'), '.cache', 'miopen')
if os.path.exists(cache_dir):
    import shutil
    try:
        shutil.rmtree(cache_dir)
        print(f"[Info] Existing MIOpen cache deleted: {cache_dir}")
    except Exception as e:
        print(f"[Warning] Failed to delete MIOpen cache: {e}")
os.makedirs(cache_dir, exist_ok=True)
os.environ['MIOPEN_USER_DB_PATH'] = cache_dir

# MIOpen environment variables
os.environ['MIOPEN_FORCE_LOGGING'] = '0'
os.environ['MIOPEN_LOG_LEVEL'] = '0'

# GPU 최적화 설정
# MIOpen 오류 방지를 위해 cudnn.enabled = False로 설정 (필수)
# On ROCm, cudnn refers to MIOpen, so setting False uses default implementation
torch.backends.cudnn.enabled = False


from pyannote.audio import Pipeline

# --- Configuration ---
# Enter the path to the audio file you want to test here.
# If the file is in the same folder as the script, just enter the filename.
audio_file = "sample.wav"  # 테스트할 오디오 파일
# --- End Configuration ---

# Check if file exists
if not os.path.exists(audio_file):
    print(f"Error: File '{audio_file}' not found.")
    print("Please place the audio file in the same folder as the script, or modify the file path correctly.")
    exit()

# Initialize logger
logger = DiarizationLogger(log_dir="logs")

print("Starting GPU setup and pipeline loading...")
logger.log_info("Starting GPU setup and pipeline loading...")

# 1. GPU device setup
use_gpu = torch.cuda.is_available()
if use_gpu:
    device = torch.device("cuda")
    gpu_name = torch.cuda.get_device_name(0)
    
    # GPU 메모리 캐시 초기화
    torch.cuda.empty_cache()
    
    # GPU 최적화 설정 확인 및 출력
    print(f"GPU detected: {gpu_name}")
    print(f"[GPU Settings] cudnn.enabled: {torch.backends.cudnn.enabled} (MIOpen 오류 방지)")
    print(f"[GPU Settings] cudnn.benchmark: {torch.backends.cudnn.benchmark}")
    print(f"[GPU Settings] matmul.allow_tf32: {torch.backends.cuda.matmul.allow_tf32}")
    if hasattr(torch, 'get_float32_matmul_precision'):
        try:
            precision = torch.get_float32_matmul_precision()
            print(f"[GPU Settings] matmul precision: {precision}")
        except Exception:
            pass
    
    logger.log_info(f"GPU detected: {gpu_name}")
    logger.log_info(f"cudnn.enabled: {torch.backends.cudnn.enabled}")
    logger.log_info(f"cudnn.benchmark: {torch.backends.cudnn.benchmark}")
    logger.log_info(f"matmul.allow_tf32: {torch.backends.cuda.matmul.allow_tf32}")
else:
    device = torch.device("cpu")
    print("GPU not found. Running on CPU.")
    logger.log_info("GPU not found. Running on CPU.")

# 2. Load speaker diarization pipeline
model_load_start = time.time()
try:
    print("Loading pyannote/speaker-diarization-3.1 model...")
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1"
    )
    
    # Run on GPU
    pipeline.to(device)
    
    # Set all models to eval mode (prevent MIOpen errors)
    if hasattr(pipeline, '_segmentation') and hasattr(pipeline._segmentation, 'model'):
        pipeline._segmentation.model.eval()
    if hasattr(pipeline, '_embedding') and hasattr(pipeline._embedding, 'model'):
        pipeline._embedding.model.eval()
    
    # GPU 메모리 정리
    if use_gpu:
        torch.cuda.empty_cache()
        # 모델 로드 후 GPU 메모리 사용량 확인
        gpu_memory_after_load = torch.cuda.memory_allocated(0)/1024**3
        print(f"[Info] 모델 로드 후 GPU 메모리 사용량: {gpu_memory_after_load:.2f} GB")
    
    model_load_time = time.time() - model_load_start
    print(f"Speaker diarization pipeline loaded and moved to GPU successfully! (로드 시간: {model_load_time:.2f}초)")
    logger.log_info(f"Speaker diarization pipeline loaded and moved to GPU successfully! (로드 시간: {model_load_time:.2f}초)")
        
except Exception as e:
    import traceback
    error_traceback = traceback.format_exc()
    print(f"Pipeline loading failed: {e}")
    print("\nDetailed error information:")
    print(error_traceback)
    logger.log_error(f"Pipeline loading failed: {e}", error_traceback)
    logger.finish()
    print("\nPlease verify that you have accepted the terms on the Hugging Face model page.")
    exit()

# 3. Load audio file into memory
print(f"\nLoading '{audio_file}' file into memory...")
audio_load_start = time.time()
waveform, sample_rate = librosa.load(audio_file, sr=16000)
audio_load_time = time.time() - audio_load_start
print(f"Audio file loaded in {audio_load_time:.2f} seconds")

# Calculate audio file length (HH:MM:SS format)
audio_duration_seconds = len(waveform) / sample_rate
audio_hours = int(audio_duration_seconds // 3600)
audio_minutes = int((audio_duration_seconds % 3600) // 60)
audio_secs = int(audio_duration_seconds % 60)
print(f"Audio file length: {audio_hours:02d}:{audio_minutes:02d}:{audio_secs:02d} ({audio_duration_seconds:.2f} seconds)")

# Log audio information
logger.log_audio_info(audio_file, audio_duration_seconds, sample_rate)

# Move input data to GPU (enable GPU acceleration)
audio_data = {
    "waveform": torch.from_numpy(waveform).unsqueeze(0).to(device),  # (1, time) - moved to GPU
    "sample_rate": sample_rate
}

# GPU 모니터링 클래스
class GPUMonitor:
    def __init__(self, device, interval=0.5):
        self.device = device
        self.interval = interval
        self.monitoring = False
        self.stats = {
            'max_memory_allocated': 0,
            'max_memory_reserved': 0,
            'max_memory_usage': 0,
            'samples': []
        }
        self.thread = None
    
    def get_gpu_stats(self):
        """GPU 통계 수집"""
        if not torch.cuda.is_available():
            return None
        
        memory_allocated = torch.cuda.memory_allocated(self.device) / 1024**3  # GB
        memory_reserved = torch.cuda.memory_reserved(self.device) / 1024**3  # GB
        
        # ROCm에서 GPU 사용률은 직접 측정이 어려우므로 메모리 사용률로 대체
        try:
            # 전체 GPU 메모리 가져오기
            if hasattr(torch.cuda, 'get_device_properties'):
                props = torch.cuda.get_device_properties(self.device)
                total_memory = props.total_memory / 1024**3  # GB
                memory_usage_percent = (memory_reserved / total_memory) * 100 if total_memory > 0 else 0
            else:
                total_memory = 0
                memory_usage_percent = 0
        except Exception:
            total_memory = 0
            memory_usage_percent = 0
        
        return {
            'memory_allocated_gb': memory_allocated,
            'memory_reserved_gb': memory_reserved,
            'memory_usage_percent': memory_usage_percent,
            'total_memory_gb': total_memory,
            'timestamp': time.time()
        }
    
    def monitor_loop(self):
        """모니터링 루프"""
        while self.monitoring:
            stats = self.get_gpu_stats()
            if stats:
                self.stats['samples'].append(stats)
                self.stats['max_memory_allocated'] = max(
                    self.stats['max_memory_allocated'], 
                    stats['memory_allocated_gb']
                )
                self.stats['max_memory_reserved'] = max(
                    self.stats['max_memory_reserved'], 
                    stats['memory_reserved_gb']
                )
                self.stats['max_memory_usage'] = max(
                    self.stats['max_memory_usage'], 
                    stats['memory_usage_percent']
                )
                
                # 실시간 출력 (간소화)
                if len(self.stats['samples']) % 2 == 0:  # 1초마다 출력
                    print(f"\r[GPU Monitor] Memory: {stats['memory_reserved_gb']:.2f}GB / {stats['total_memory_gb']:.1f}GB ({stats['memory_usage_percent']:.1f}%) | Allocated: {stats['memory_allocated_gb']:.2f}GB", end='', flush=True)
            
            time.sleep(self.interval)
    
    def start(self):
        """모니터링 시작"""
        self.monitoring = True
        self.stats = {
            'max_memory_allocated': 0,
            'max_memory_reserved': 0,
            'max_memory_usage': 0,
            'samples': []
        }
        self.thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """모니터링 중지"""
        self.monitoring = False
        if self.thread:
            self.thread.join(timeout=1.0)
        print()  # 줄바꿈
    
    def get_summary(self):
        """모니터링 요약"""
        if not self.stats['samples']:
            return None
        
        avg_memory = sum(s['memory_reserved_gb'] for s in self.stats['samples']) / len(self.stats['samples'])
        avg_usage = sum(s['memory_usage_percent'] for s in self.stats['samples']) / len(self.stats['samples'])
        
        return {
            'max_memory_allocated_gb': self.stats['max_memory_allocated'],
            'max_memory_reserved_gb': self.stats['max_memory_reserved'],
            'max_memory_usage_percent': self.stats['max_memory_usage'],
            'avg_memory_reserved_gb': avg_memory,
            'avg_memory_usage_percent': avg_usage,
            'sample_count': len(self.stats['samples'])
        }

# 4. Run speaker diarization
print(f"Starting speaker diarization for '{audio_file}'...")
diarization_start = time.time()

# GPU 모니터링 시작
gpu_monitor = None
if use_gpu:
    gpu_monitor = GPUMonitor(device, interval=0.5)
    print("\n[GPU Monitor] 실시간 GPU 모니터링 시작...")
    gpu_monitor.start()

# Run in inference mode (disable gradient calculation)
with torch.no_grad():
    # Verify GPU usage
    print(f"\n[Check] Current device: {device}")
    print(f"[Check] PyTorch CUDA available: {torch.cuda.is_available()}")
    print(f"[Check] Current GPU: {torch.cuda.current_device()}")
    print(f"[Check] GPU name: {torch.cuda.get_device_name(0)}")
    gpu_memory_allocated = torch.cuda.memory_allocated(0)/1024**3
    gpu_memory_reserved = torch.cuda.memory_reserved(0)/1024**3
    cudnn_enabled = torch.backends.cudnn.enabled
    
    print(f"[Check] GPU memory allocated: {gpu_memory_allocated:.2f} GB")
    print(f"[Check] GPU memory reserved: {gpu_memory_reserved:.2f} GB")
    print(f"[Check] torch.backends.cudnn.enabled: {cudnn_enabled}")
    print(f"[Check] cudnn.benchmark: {torch.backends.cudnn.benchmark}")
    print(f"[Check] matmul.allow_tf32: {torch.backends.cuda.matmul.allow_tf32}")
    
    # Log GPU information
    logger.log_gpu_info(
        gpu_name=torch.cuda.get_device_name(0),
        cuda_available=torch.cuda.is_available(),
        cudnn_enabled=cudnn_enabled,
        memory_allocated_gb=gpu_memory_allocated,
        memory_reserved_gb=gpu_memory_reserved,
    )
    
    print(f"\n[Check] Verify models are on GPU:")
    if hasattr(pipeline, '_segmentation') and hasattr(pipeline._segmentation, 'model'):
        seg_device = next(pipeline._segmentation.model.parameters()).device
        print(f"  - Segmentation model: {seg_device}")
    if hasattr(pipeline, '_embedding') and hasattr(pipeline._embedding, 'model'):
        emb_device = next(pipeline._embedding.model.parameters()).device
        print(f"  - Embedding model: {emb_device}")
    # Verify input tensor is also on GPU
    print(f"  - Input data device: {audio_data['waveform'].device}")
    
    # GPU 최적화 상태 안내
    if cudnn_enabled:
        print(f"\n[Info] MIOpen (cudnn) 최적화 활성화됨 - 최대 GPU 성능 모드")
    else:
        print(f"\n[Note] torch.backends.cudnn.enabled = False로 설정됨.")
        print(f"[Note] MIOpen 최적화가 비활성화되어 일부 연산이 CPU로 폴백될 수 있습니다.")
        print(f"[Note] 하지만 MIOpen 오류를 방지합니다.")
    
    logger.log_settings(
        cudnn_enabled=cudnn_enabled,
        device=str(device),
        miopen_cache_disabled=True,
    )
    
    print("\n[Start] Starting speaker diarization...")
    logger.log_info("[Start] Starting speaker diarization...")
    
    try:
        diarization = pipeline(audio_data)
    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        logger.log_error(f"Error during speaker diarization: {e}", error_traceback)
        logger.finish()
        raise

# GPU 모니터링 중지
if gpu_monitor:
    gpu_monitor.stop()
    print("\n[GPU Monitor] 모니터링 중지됨")

diarization_end = time.time()
diarization_time = diarization_end - diarization_start
total_time = diarization_end - model_load_start

# GPU 모니터링 결과 출력
if gpu_monitor:
    summary = gpu_monitor.get_summary()
    if summary:
        print(f"\n{'='*60}")
        print(f"GPU 사용률 분석 (GPU Usage Analysis):")
        print(f"{'='*60}")
        print(f"최대 메모리 할당: {summary['max_memory_allocated_gb']:.2f} GB")
        print(f"최대 메모리 예약: {summary['max_memory_reserved_gb']:.2f} GB")
        print(f"최대 메모리 사용률: {summary['max_memory_usage_percent']:.1f}%")
        print(f"평균 메모리 예약: {summary['avg_memory_reserved_gb']:.2f} GB")
        print(f"평균 메모리 사용률: {summary['avg_memory_usage_percent']:.1f}%")
        print(f"샘플 수: {summary['sample_count']}개")
        print(f"{'='*60}")

# 각 단계별 시간 출력
print(f"\n{'='*60}")
print(f"시간 분석 (Time Breakdown):")
print(f"{'='*60}")
print(f"모델 로드 시간: {model_load_time:.2f}초 ({model_load_time/total_time*100:.1f}%)")
print(f"오디오 파일 로드 시간: {audio_load_time:.2f}초 ({audio_load_time/total_time*100:.1f}%)")
print(f"화자 분리 처리 시간: {diarization_time:.2f}초 ({diarization_time/total_time*100:.1f}%)")
print(f"총 실행 시간: {total_time:.2f}초")
print(f"{'='*60}")

elapsed_time = diarization_time

# Convert elapsed time to HH:MM:SS format
elapsed_hours = int(elapsed_time // 3600)
elapsed_minutes = int((elapsed_time % 3600) // 60)
elapsed_secs = int(elapsed_time % 60)
elapsed_milliseconds = int((elapsed_time % 1) * 1000)

# Collect speaker diarization results
segments = list(diarization.itertracks(yield_label=True))
unique_speakers = set(speaker for _, _, speaker in segments)
num_speakers = len(unique_speakers)
num_segments = len(segments)
processing_speed = audio_duration_seconds / elapsed_time

# Log results
logger.log_result(
    elapsed_time=elapsed_time,
    num_speakers=num_speakers,
    num_segments=num_segments,
    processing_speed=processing_speed,
    audio_duration=audio_duration_seconds,
)

print(f"\n{'='*60}")
print(f"Speaker diarization completed!")
print(f"{'='*60}")
print(f"Audio file length: {audio_hours:02d}:{audio_minutes:02d}:{audio_secs:02d} ({audio_duration_seconds:.2f} seconds)")
print(f"Processing time: {elapsed_hours:02d}:{elapsed_minutes:02d}:{elapsed_secs:02d}.{elapsed_milliseconds:03d} ({elapsed_time:.2f} seconds)")
print(f"Number of speakers detected: {num_speakers}")
print(f"Number of segments: {num_segments}")
print(f"Processing speed: {processing_speed:.2f}x (real-time ratio: {elapsed_time/audio_duration_seconds:.2f}x)")
print(f"{'='*60}")

# 5. Output results
print("\n--- Speaker Diarization Results ---")
logger.log_segments(segments)

# Finish logging
log_file, json_file = logger.finish()
print(f"\nLog files saved:")
print(f"  - Log: {log_file}")
print(f"  - JSON: {json_file}")
