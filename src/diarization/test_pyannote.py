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

# MIOpen optimization settings based on GitHub issue #150168
# Reference: https://github.com/pytorch/pytorch/issues/150168

# MIOpen mode selection
# 
# MIOPEN_FIND_MODE 옵션 설명:
# - NORMAL: 최적 커널을 찾기 위해 다양한 알고리즘을 평가 (느리지만 정확)
# - FAST: 제한된 알고리즘만 평가하여 빠르게 선택 (빠르지만 최적화 제한)
# - IMMEDIATE (또는 '3'): 커널 검색/컴파일 건너뛰고 즉시 실행 (가장 빠르지만 최적화 없음)
# - HYBRID: NORMAL과 FAST의 균형 (MIOpen 버전에 따라 지원)
# 
# 모드별 설정:
# 0: MIOpen completely disabled (most stable, currently working)
# 1: FAST mode (SQLite error occurs)
# 2: Cache completely disabled (worth trying)
# 3: IMMEDIATE mode (compile skip, immediate execution)
# 4: Minimum functionality mode (last resort)
# 5: Try to set include paths for rocRAND headers (cache enabled)
# 6: NORMAL mode - MIOpen enabled with cache disabled + benchmark (145.86s for 885s audio)
# 7: IMMEDIATE mode - Skip kernel search (MIOpen 오류 발생 - 사용 불가)
# 8: FAST mode - Limited algorithm search, fastest verified (94.82s for 885s audio) ⭐ RECOMMENDED
MIOPEN_MODE = 8  # Select 0~8 (Mode 8: FAST mode - 최적 설정)

# MIOpen cache directory setup (will be configured after mode selection)
# Cache stores optimized kernels for faster subsequent runs
# First run: MIOpen finds optimal kernels and stores them in cache
# Subsequent runs: Cached kernels are reused, skipping compilation/search time
cache_dir = os.path.join(os.path.expanduser('~'), '.cache', 'miopen')
preserve_cache = False  # Will be set to True in Mode 5 if cache is enabled

if MIOPEN_MODE == 0:
    # Mode 0: MIOpen completely disabled (stable, verified)
    print("[Info] Mode 0: MIOpen disabled (stable)")
    USE_MIOPEN_OPTIMIZATION = False
    
elif MIOPEN_MODE == 1:
    # Mode 1: FAST mode (SQLite error - confirmed failure)
    print("[Info] Mode 1: MIOpen FAST mode (SQLite error risk)")
    os.environ['MIOPEN_FIND_MODE'] = 'FAST'
    os.environ['MIOPEN_DISABLE_CACHE'] = '0'
    USE_MIOPEN_OPTIMIZATION = True
    
elif MIOPEN_MODE == 2:
    # Mode 2: MIOpen enabled + cache completely disabled
    print("[Info] Mode 2: MIOpen enabled + cache completely disabled")
    os.environ['MIOPEN_FIND_MODE'] = 'NORMAL'
    os.environ['MIOPEN_DISABLE_CACHE'] = '1'  # Disable cache completely
    os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'  # Disable Find DB
    USE_MIOPEN_OPTIMIZATION = True
    
elif MIOPEN_MODE == 3:
    # Mode 3: IMMEDIATE mode (compile skip, immediate execution)
    print("[Info] Mode 3: MIOpen IMMEDIATE mode (compile skip)")
    os.environ['MIOPEN_FIND_MODE'] = 'IMMEDIATE'  # Fastest mode (no optimization)
    os.environ['MIOPEN_DISABLE_CACHE'] = '1'
    os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'
    USE_MIOPEN_OPTIMIZATION = True
    
elif MIOPEN_MODE == 4:
    # Mode 4: All MIOpen optimizations disabled
    print("[Info] Mode 4: MIOpen minimum functionality mode")
    os.environ['MIOPEN_FIND_MODE'] = '3'  # IMMEDIATE mode (specified by number)
    os.environ['MIOPEN_DISABLE_CACHE'] = '1'
    os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'
    os.environ['MIOPEN_FIND_ENFORCE'] = 'NONE'
    os.environ['MIOPEN_DEBUG_CONV_IMPLICIT_GEMM'] = '0'
    USE_MIOPEN_OPTIMIZATION = True
    
elif MIOPEN_MODE == 5:
    # Mode 5: Try to fix rocRAND header issue by setting include paths
    print("[Info] Mode 5: MIOpen with rocRAND header path fix")
    import rocm_sdk_devel
    import rocm_sdk_core
    devel_path = os.path.dirname(rocm_sdk_devel.__file__)
    core_path = os.path.dirname(rocm_sdk_core.__file__)
    site_lib_path = os.path.dirname(devel_path)
    # Try to find include directories from extracted _rocm_sdk_devel and rocm_sdk_core
    possible_include_paths = [
        os.path.join(site_lib_path, '_rocm_sdk_devel', '_rocm_sdk_devel', 'include'),
        os.path.join(site_lib_path, '_rocm_sdk_core', 'include'),  # rocm-sdk-core include
        os.path.join(core_path, '..', '_rocm_sdk_core', 'include'),  # Alternative path
        os.path.join(devel_path, 'include'),
        os.path.join(devel_path, 'rocm', 'include'),
        os.path.join(devel_path, '..', 'rocm_sdk_libraries', 'include'),
    ]
    include_paths = []
    for path in possible_include_paths:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            include_paths.append(abs_path)
            print(f"[Info] Found include path: {abs_path}")
    
    # Verify rocrand_xorwow.h exists
    rocrand_path = os.path.join(site_lib_path, '_rocm_sdk_devel', '_rocm_sdk_devel', 'include', 'rocrand', 'rocrand_xorwow.h')
    if os.path.exists(rocrand_path):
        print(f"[Info] Found rocrand_xorwow.h at: {rocrand_path}")
    else:
        print(f"[Warning] rocrand_xorwow.h not found at expected path")
    
    # Set HIP include paths if found
    if include_paths:
        # Use semicolon for Windows path separator
        include_path_str = ';'.join(include_paths)
        os.environ['HIP_INCLUDE_PATH'] = include_path_str
        os.environ['HIP_PLATFORM'] = 'amd'
        # Set ROCM_PATH for compatibility
        os.environ['ROCM_PATH'] = os.path.dirname(include_paths[0])
        # Also set CPLUS_INCLUDE_PATH and C_INCLUDE_PATH for Windows
        os.environ['CPLUS_INCLUDE_PATH'] = include_path_str
        os.environ['C_INCLUDE_PATH'] = include_path_str
        # Set HIP compiler flags with include paths
        # Format: -Ipath1 -Ipath2 (space-separated for Windows)
        hip_include_flags = ' '.join([f'-I{path}' for path in include_paths])
        os.environ['HIPCC_COMPILE_FLAGS_APPEND'] = hip_include_flags
        os.environ['HIP_INCLUDE_PATH'] = include_path_str
        print(f"[Info] Set HIP_INCLUDE_PATH: {include_path_str}")
        print(f"[Info] Set ROCM_PATH: {os.environ['ROCM_PATH']}")
        print(f"[Info] Set HIPCC_COMPILE_FLAGS_APPEND: {hip_include_flags}")
    
    # Use NORMAL mode - it's slower on first run but more stable
    # Try enabling cache for better performance on subsequent runs
    # Cache stores optimized kernels so they don't need to be recompiled
    os.environ['MIOPEN_FIND_MODE'] = 'NORMAL'
    os.environ['MIOPEN_DISABLE_CACHE'] = '0'  # Enable cache - test if SQLite issue is resolved
    # Keep FIND_DB disabled to avoid SQLite errors
    os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'
    print("[Info] MIOpen cache enabled - optimized kernels will be cached for faster subsequent runs")
    preserve_cache = True  # Mark that we want to preserve cache
    USE_MIOPEN_OPTIMIZATION = True

elif MIOPEN_MODE == 6:
    # Mode 6: Fastest verified configuration (145.86s for 885s audio = 6.07x speed)
    # Based on successful run: 2025-11-06 14:01:59
    print("[Info] Mode 6: Fastest mode - MIOpen with cache disabled + benchmark")
    import rocm_sdk_devel
    import rocm_sdk_core
    devel_path = os.path.dirname(rocm_sdk_devel.__file__)
    core_path = os.path.dirname(rocm_sdk_core.__file__)
    site_lib_path = os.path.dirname(devel_path)
    # Set include paths for rocRAND headers (same as Mode 5)
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
            print(f"[Info] Found include path: {abs_path}")
    
    # Set HIP include paths if found
    if include_paths:
        include_path_str = ';'.join(include_paths)
        os.environ['HIP_INCLUDE_PATH'] = include_path_str
        os.environ['HIP_PLATFORM'] = 'amd'
        os.environ['ROCM_PATH'] = os.path.dirname(include_paths[0])
        os.environ['CPLUS_INCLUDE_PATH'] = include_path_str
        os.environ['C_INCLUDE_PATH'] = include_path_str
        hip_include_flags = ' '.join([f'-I{path}' for path in include_paths])
        os.environ['HIPCC_COMPILE_FLAGS_APPEND'] = hip_include_flags
        print(f"[Info] Set HIP include paths")
    
    # Fastest configuration: NORMAL mode + cache DISABLED + benchmark enabled
    os.environ['MIOPEN_FIND_MODE'] = 'NORMAL'
    os.environ['MIOPEN_DISABLE_CACHE'] = '1'  # Cache disabled for faster performance
    os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'  # Disable Find DB to avoid SQLite errors
    print("[Info] MIOpen cache disabled - fastest configuration (verified: 145.86s for 885s audio)")
    preserve_cache = False  # Don't preserve cache
    USE_MIOPEN_OPTIMIZATION = True

elif MIOPEN_MODE == 7:
    # Mode 7: IMMEDIATE mode - Skip kernel search/compilation, fastest startup
    # Expected: Very fast first run, but may be slower overall due to no optimization
    print("[Info] Mode 7: IMMEDIATE mode - Skip kernel search for fastest startup")
    import rocm_sdk_devel
    import rocm_sdk_core
    devel_path = os.path.dirname(rocm_sdk_devel.__file__)
    core_path = os.path.dirname(rocm_sdk_core.__file__)
    site_lib_path = os.path.dirname(devel_path)
    # Set include paths for rocRAND headers (required for MIOpen)
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
            print(f"[Info] Found include path: {abs_path}")
    
    # Set HIP include paths if found
    if include_paths:
        include_path_str = ';'.join(include_paths)
        os.environ['HIP_INCLUDE_PATH'] = include_path_str
        os.environ['HIP_PLATFORM'] = 'amd'
        os.environ['ROCM_PATH'] = os.path.dirname(include_paths[0])
        os.environ['CPLUS_INCLUDE_PATH'] = include_path_str
        os.environ['C_INCLUDE_PATH'] = include_path_str
        hip_include_flags = ' '.join([f'-I{path}' for path in include_paths])
        os.environ['HIPCC_COMPILE_FLAGS_APPEND'] = hip_include_flags
        print(f"[Info] Set HIP include paths")
    
    # IMMEDIATE mode: Skip kernel search, use default kernels immediately
    os.environ['MIOPEN_FIND_MODE'] = 'IMMEDIATE'  # 또는 '3'
    os.environ['MIOPEN_DISABLE_CACHE'] = '1'  # Cache disabled
    os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'  # Disable Find DB
    print("[Info] IMMEDIATE mode - will skip kernel search/compilation (fastest startup, may be slower overall)")
    preserve_cache = False
    USE_MIOPEN_OPTIMIZATION = True

elif MIOPEN_MODE == 8:
    # Mode 8: FAST mode - Limited algorithm search for faster kernel selection
    # Try with rocRAND header paths to avoid previous SQLite errors
    print("[Info] Mode 8: FAST mode - Limited algorithm search (faster than NORMAL)")
    import rocm_sdk_devel
    import rocm_sdk_core
    devel_path = os.path.dirname(rocm_sdk_devel.__file__)
    core_path = os.path.dirname(rocm_sdk_core.__file__)
    site_lib_path = os.path.dirname(devel_path)
    # Set include paths for rocRAND headers (same as Mode 5/6)
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
            print(f"[Info] Found include path: {abs_path}")
    
    # Set HIP include paths if found
    if include_paths:
        include_path_str = ';'.join(include_paths)
        os.environ['HIP_INCLUDE_PATH'] = include_path_str
        os.environ['HIP_PLATFORM'] = 'amd'
        os.environ['ROCM_PATH'] = os.path.dirname(include_paths[0])
        os.environ['CPLUS_INCLUDE_PATH'] = include_path_str
        os.environ['C_INCLUDE_PATH'] = include_path_str
        hip_include_flags = ' '.join([f'-I{path}' for path in include_paths])
        os.environ['HIPCC_COMPILE_FLAGS_APPEND'] = hip_include_flags
        print(f"[Info] Set HIP include paths")
    
    # FAST mode: Limited algorithm search (faster than NORMAL, but may find less optimal kernels)
    os.environ['MIOPEN_FIND_MODE'] = 'FAST'
    os.environ['MIOPEN_DISABLE_CACHE'] = '1'  # Cache disabled for faster performance
    os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'  # Disable Find DB to avoid SQLite errors
    print("[Info] FAST mode - will search limited algorithms (faster startup, may be slower overall)")
    preserve_cache = False
    USE_MIOPEN_OPTIMIZATION = True

# Handle cache directory based on preserve_cache flag
if preserve_cache:
    # Cache enabled - keep existing cache for faster subsequent runs
    print(f"[Info] MIOpen cache will be preserved: {cache_dir}")
    if os.path.exists(cache_dir):
        cache_size = sum(os.path.getsize(os.path.join(dirpath, filename))
                        for dirpath, dirnames, filenames in os.walk(cache_dir)
                        for filename in filenames) / (1024**2)  # MB
        print(f"[Info] Existing cache found ({cache_size:.2f} MB) - will reuse optimized kernels")
else:
    # Cache disabled or other mode - delete cache to prevent SQLite errors
    if os.path.exists(cache_dir):
        import shutil
        try:
            shutil.rmtree(cache_dir)
            print(f"[Info] Existing MIOpen cache deleted: {cache_dir}")
        except Exception as e:
            print(f"[Warning] Failed to delete MIOpen cache: {e}")
os.makedirs(cache_dir, exist_ok=True)
os.environ['MIOPEN_USER_DB_PATH'] = cache_dir

# GPU optimization settings

if USE_MIOPEN_OPTIMIZATION:
    print("[Info] MIOpen optimization mode enabled")
    torch.backends.cudnn.enabled = True
    # Enable benchmark for better performance - finds optimal algorithm once and reuses it
    # This is especially beneficial for repeated operations with same input sizes
    # Mode 6: benchmark=True for fastest performance (verified 145.86s)
    # Mode 7: IMMEDIATE mode - benchmark may not help much (no kernel search anyway)
    # Mode 8: FAST mode - benchmark enabled for better performance
    if MIOPEN_MODE == 6:
        torch.backends.cudnn.benchmark = True
        print("[Info] cudnn.benchmark enabled (Mode 6: fastest verified config)")
    elif MIOPEN_MODE == 7:
        torch.backends.cudnn.benchmark = False  # IMMEDIATE mode doesn't need benchmark
        print("[Info] cudnn.benchmark disabled (Mode 7: IMMEDIATE - no kernel search)")
    elif MIOPEN_MODE == 8:
        torch.backends.cudnn.benchmark = True  # FAST mode can benefit from benchmark
        print("[Info] cudnn.benchmark enabled (Mode 8: FAST mode)")
    else:
        torch.backends.cudnn.benchmark = True  # Enable for better performance
        print("[Info] cudnn.benchmark enabled - will optimize algorithms on first run")
else:
    print("[Info] MIOpen disabled mode (stable)")
    # Set cudnn.enabled = False to prevent MIOpen errors
    # On ROCm, cudnn refers to MIOpen, so setting False uses default implementation
    torch.backends.cudnn.enabled = False


from pyannote.audio import Pipeline

# --- Configuration ---
# Enter the path to the audio file you want to test here.
# All audio files are now in the media/wav/ directory.
# 프로젝트 루트 기준 경로로 설정
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(script_dir))  # src/diarization -> src -> project_root

# audio_file = os.path.join(project_root, "media", "wav", "sample.wav")  # Audio file to test (33초)
audio_file = os.path.join(project_root, "media", "wav", "audio_for_whisper_tariff.wav")  # Audio file to test (14.75분)
# audio_file = os.path.join(project_root, "media", "wav", "xz-librazy-56m.wav")  # Audio file to test (56분)
# audio_file = os.path.join(project_root, "media", "wav", "president-2h.wav")  # Audio file to test (약 2시간)
# audio_file = os.path.join(project_root, "media", "wav", "president-100days-3h.wav")  # Audio file to test (약 3시간)
# --- End Configuration ---

# Check if file exists
if not os.path.exists(audio_file):
    print(f"Error: File '{audio_file}' not found.")
    print("Please place the audio file in the same folder as the script, or modify the file path correctly.")
    exit()

# Initialize logger
# 로그 디렉토리 설정 (화자분리 전용 logs 폴더)
script_dir = os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(script_dir, "logs")
logger = DiarizationLogger(log_dir=log_dir)

print("Starting GPU setup and pipeline loading...")
logger.log_info("Starting GPU setup and pipeline loading...")

# 1. GPU device setup
use_gpu = torch.cuda.is_available()
if use_gpu:
    device = torch.device("cuda")
    try:
        gpu_name = torch.cuda.get_device_name(0)
    except (UnicodeDecodeError, RuntimeError) as e:
        # Fallback for encoding issues or initialization errors
        gpu_name = "AMD GPU (name unavailable)"
        print(f"[Warning] Could not get GPU name: {e}")
    
    # Clear GPU memory cache
    torch.cuda.empty_cache()
    
    # Check and display GPU optimization settings
    print(f"GPU detected: {gpu_name}")
    print(f"[GPU Settings] cudnn.enabled: {torch.backends.cudnn.enabled} (Prevent MIOpen errors)")
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
    
    # Optimize pipeline settings for better performance
    # Try to set batch size if available
    # Note: Batch size changes showed minimal impact on processing time
    # GPU utilization drops during certain phases is a characteristic of pyannote's processing
    # Tested: 32 (baseline), 128 (no improvement), 512 (10.8% slower) - 32 is optimal
    SEGMENTATION_BATCH_SIZE = 32  # Default batch size (tested: 128 and 512 showed no improvement or slower)
    EMBEDDING_BATCH_SIZE = 32     # Default batch size
    
    if hasattr(pipeline, '_segmentation'):
        # 배치 크기 설정
        if hasattr(pipeline._segmentation, 'batch_size'):
            pipeline._segmentation.batch_size = SEGMENTATION_BATCH_SIZE
            print(f"[Info] Segmentation batch size set to {SEGMENTATION_BATCH_SIZE}")
    if hasattr(pipeline, '_embedding'):
        if hasattr(pipeline._embedding, 'batch_size'):
            pipeline._embedding.batch_size = EMBEDDING_BATCH_SIZE
            print(f"[Info] Embedding batch size set to {EMBEDDING_BATCH_SIZE}")
    
    # Set all models to eval mode (prevent MIOpen errors)
    if hasattr(pipeline, '_segmentation') and hasattr(pipeline._segmentation, 'model'):
        pipeline._segmentation.model.eval()
    if hasattr(pipeline, '_embedding') and hasattr(pipeline._embedding, 'model'):
        pipeline._embedding.model.eval()
    
    # Clear GPU memory
    if use_gpu:
        torch.cuda.empty_cache()
        # Check GPU memory usage after model loading
        gpu_memory_after_load = torch.cuda.memory_allocated(0)/1024**3
        print(f"[Info] GPU memory usage after model loading: {gpu_memory_after_load:.2f} GB")
    
    model_load_time = time.time() - model_load_start
    print(f"Speaker diarization pipeline loaded and moved to GPU successfully! (Load time: {model_load_time:.2f}s)")
    logger.log_info(f"Speaker diarization pipeline loaded and moved to GPU successfully! (Load time: {model_load_time:.2f}s)")
        
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
# Use non_blocking=True for async transfer (if supported)
audio_data = {
    "waveform": torch.from_numpy(waveform).unsqueeze(0).to(device, non_blocking=True),  # (1, time) - moved to GPU with async transfer
    "sample_rate": sample_rate
}

# GPU monitoring class
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
        """Collect GPU statistics"""
        if not torch.cuda.is_available():
            return None
        
        memory_allocated = torch.cuda.memory_allocated(self.device) / 1024**3  # GB
        memory_reserved = torch.cuda.memory_reserved(self.device) / 1024**3  # GB
        
        # In ROCm, GPU utilization is difficult to measure directly, so use memory usage as a proxy
        try:
            # Get total GPU memory
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
        """Monitoring loop"""
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
                
                # Real-time output (simplified)
                if len(self.stats['samples']) % 2 == 0:  # Output every 1 second
                    print(f"\r[GPU Monitor] Memory: {stats['memory_reserved_gb']:.2f}GB / {stats['total_memory_gb']:.1f}GB ({stats['memory_usage_percent']:.1f}%) | Allocated: {stats['memory_allocated_gb']:.2f}GB", end='', flush=True)
            
            time.sleep(self.interval)
    
    def start(self):
        """Start monitoring"""
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
        """Stop monitoring"""
        self.monitoring = False
        if self.thread:
            self.thread.join(timeout=1.0)
        print()  # New line
    
    def get_summary(self):
        """Monitoring summary"""
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

# Start GPU monitoring
gpu_monitor = None
if use_gpu:
    gpu_monitor = GPUMonitor(device, interval=0.5)
    print("\n[GPU Monitor] Starting real-time GPU monitoring...")
    gpu_monitor.start()

# Run in inference mode (disable gradient calculation and autograd)
# Using inference_mode() is more efficient than no_grad() for inference
with torch.inference_mode():
    # Verify GPU usage
    print(f"\n[Check] Current device: {device}")
    print(f"[Check] PyTorch CUDA available: {torch.cuda.is_available()}")
    print(f"[Check] Current GPU: {torch.cuda.current_device()}")
    try:
        gpu_check_name = torch.cuda.get_device_name(0)
        print(f"[Check] GPU name: {gpu_check_name}")
    except (UnicodeDecodeError, RuntimeError):
        print(f"[Check] GPU name: {gpu_name}")
    gpu_memory_allocated = torch.cuda.memory_allocated(0)/1024**3
    gpu_memory_reserved = torch.cuda.memory_reserved(0)/1024**3
    cudnn_enabled = torch.backends.cudnn.enabled
    
    print(f"[Check] GPU memory allocated: {gpu_memory_allocated:.2f} GB")
    print(f"[Check] GPU memory reserved: {gpu_memory_reserved:.2f} GB")
    print(f"[Check] torch.backends.cudnn.enabled: {cudnn_enabled}")
    print(f"[Check] cudnn.benchmark: {torch.backends.cudnn.benchmark}")
    print(f"[Check] matmul.allow_tf32: {torch.backends.cuda.matmul.allow_tf32}")
    
    # Log GPU information
    try:
        log_gpu_name = torch.cuda.get_device_name(0)
    except (UnicodeDecodeError, RuntimeError):
        log_gpu_name = gpu_name
    logger.log_gpu_info(
        gpu_name=log_gpu_name,
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
    
    # GPU optimization status information
    if cudnn_enabled:
        print(f"\n[Info] MIOpen (cudnn) optimization enabled - Maximum GPU performance mode")
    else:
        print(f"\n[Note] torch.backends.cudnn.enabled set to False.")
        print(f"[Note] MIOpen optimization is disabled, some operations may fall back to CPU.")
        print(f"[Note] However, this prevents MIOpen errors.")
    
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

# Stop GPU monitoring
if gpu_monitor:
    gpu_monitor.stop()
    print("\n[GPU Monitor] Monitoring stopped")

diarization_end = time.time()
diarization_time = diarization_end - diarization_start
total_time = diarization_end - model_load_start

# Output GPU monitoring results
if gpu_monitor:
    summary = gpu_monitor.get_summary()
    if summary:
        print(f"\n{'='*60}")
        print(f"GPU Usage Analysis:")
        print(f"{'='*60}")
        print(f"Max memory allocated: {summary['max_memory_allocated_gb']:.2f} GB")
        print(f"Max memory reserved: {summary['max_memory_reserved_gb']:.2f} GB")
        print(f"Max memory usage: {summary['max_memory_usage_percent']:.1f}%")
        print(f"Avg memory reserved: {summary['avg_memory_reserved_gb']:.2f} GB")
        print(f"Avg memory usage: {summary['avg_memory_usage_percent']:.1f}%")
        print(f"Sample count: {summary['sample_count']}")
        print(f"{'='*60}")

# Output time breakdown for each stage
print(f"\n{'='*60}")
print(f"Time Breakdown:")
print(f"{'='*60}")
print(f"Model load time: {model_load_time:.2f}s ({model_load_time/total_time*100:.1f}%)")
print(f"Audio file load time: {audio_load_time:.2f}s ({audio_load_time/total_time*100:.1f}%)")
print(f"Speaker diarization processing time: {diarization_time:.2f}s ({diarization_time/total_time*100:.1f}%)")
print(f"Total execution time: {total_time:.2f}s")
print(f"{'='*60}")

elapsed_time = diarization_time

# Convert elapsed time to HH:MM:SS format
elapsed_hours = int(elapsed_time // 3600)
elapsed_minutes = int((elapsed_time % 3600) // 60)
elapsed_secs = int(elapsed_time % 60)
elapsed_milliseconds = int((elapsed_time % 1) * 1000)

# Collect speaker diarization results
# Extract segments from Annotation object using itertracks method
segments = [(turn.start, turn.end, speaker) for turn, _, speaker in diarization.itertracks(yield_label=True)]

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
