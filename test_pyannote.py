# test_pyannote.py
import torch
import time
import os
import librosa
import sys
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

# Disable MIOpen for instance_norm to prevent miopenStatusInternalError
# On ROCm, cudnn refers to MIOpen, so setting False uses default implementation
# Note: This may cause some operations to fall back to CPU, but prevents MIOpen errors
torch.backends.cudnn.enabled = False

from pyannote.audio import Pipeline

# --- Configuration ---
# Enter the path to the audio file you want to test here.
# If the file is in the same folder as the script, just enter the filename.
audio_file = "sample.wav"  # Test file
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
    print(f"GPU detected: {gpu_name}")
    logger.log_info(f"GPU detected: {gpu_name}")
else:
    device = torch.device("cpu")
    print("GPU not found. Running on CPU.")
    logger.log_info("GPU not found. Running on CPU.")

# 2. Load speaker diarization pipeline
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
    
    print("Speaker diarization pipeline loaded and moved to GPU successfully!")
    logger.log_info("Speaker diarization pipeline loaded and moved to GPU successfully!")
        
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
waveform, sample_rate = librosa.load(audio_file, sr=16000)

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

# 4. Run speaker diarization
print(f"Starting speaker diarization for '{audio_file}'...")
start_time = time.time()

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
    
    # Note about GPU usage (verify if operations actually run on GPU)
    print(f"\n[Note] torch.backends.cudnn.enabled = False is set.")
    print(f"[Note] This disables MIOpen optimization, so some operations may fall back to CPU,")
    print(f"[Note] but this prevents MIOpen errors.")
    
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

end_time = time.time()
elapsed_time = end_time - start_time

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
