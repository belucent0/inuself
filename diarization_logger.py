"""
Speaker Diarization Logging Module
Provides functionality to save work logs to files.
"""
import os
import json
from datetime import datetime
from pathlib import Path


class DiarizationLogger:
    """Speaker diarization logger"""
    
    def __init__(self, log_dir="logs"):
        """
        Initialize logger
        
        Args:
            log_dir: Directory to save log files
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        
        # Create log file paths (date-time based)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"diarization_{timestamp}.log"
        self.json_file = self.log_dir / f"diarization_{timestamp}.json"
        
        # Start logging
        self.log_data = {
            "start_time": datetime.now().isoformat(),
            "audio_file": None,
            "gpu_info": {},
            "settings": {},
            "results": {},
            "errors": [],
            "end_time": None,
        }
        
        self._write_log(f"{'='*60}")
        self._write_log(f"Speaker diarization work started: {self.log_data['start_time']}")
        self._write_log(f"{'='*60}")
    
    def _write_log(self, message, to_console=True):
        """
        Write log message to file
        
        Args:
            message: Log message
            to_console: Whether to also output to console
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] {message}\n"
        
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(log_message)
        
        if to_console:
            print(message)
    
    def log_audio_info(self, audio_file, duration_seconds, sample_rate):
        """Log audio file information"""
        self.log_data["audio_file"] = {
            "path": str(audio_file),
            "duration_seconds": duration_seconds,
            "sample_rate": sample_rate,
        }
        
        hours = int(duration_seconds // 3600)
        minutes = int((duration_seconds % 3600) // 60)
        secs = int(duration_seconds % 60)
        
        self._write_log(f"Audio file: {audio_file}")
        self._write_log(f"  - Length: {hours:02d}:{minutes:02d}:{secs:02d} ({duration_seconds:.2f} seconds)")
        self._write_log(f"  - Sample rate: {sample_rate} Hz")
    
    def log_gpu_info(self, gpu_name, cuda_available, cudnn_enabled, 
                     memory_allocated_gb, memory_reserved_gb):
        """Log GPU information"""
        self.log_data["gpu_info"] = {
            "name": gpu_name,
            "cuda_available": cuda_available,
            "cudnn_enabled": cudnn_enabled,
            "memory_allocated_gb": memory_allocated_gb,
            "memory_reserved_gb": memory_reserved_gb,
        }
        
        self._write_log(f"GPU information:")
        self._write_log(f"  - Name: {gpu_name}")
        self._write_log(f"  - CUDA available: {cuda_available}")
        self._write_log(f"  - cuDNN enabled: {cudnn_enabled}")
        self._write_log(f"  - Memory allocated: {memory_allocated_gb:.2f} GB")
        self._write_log(f"  - Memory reserved: {memory_reserved_gb:.2f} GB")
    
    def log_settings(self, **settings):
        """Log configuration information"""
        self.log_data["settings"].update(settings)
        self._write_log(f"Settings:")
        for key, value in settings.items():
            self._write_log(f"  - {key}: {value}")
    
    def log_result(self, elapsed_time, num_speakers, num_segments, 
                   processing_speed, audio_duration):
        """Log work results"""
        elapsed_hours = int(elapsed_time // 3600)
        elapsed_minutes = int((elapsed_time % 3600) // 60)
        elapsed_secs = int(elapsed_time % 60)
        elapsed_milliseconds = int((elapsed_time % 1) * 1000)
        
        self.log_data["results"] = {
            "elapsed_time_seconds": elapsed_time,
            "elapsed_time_formatted": f"{elapsed_hours:02d}:{elapsed_minutes:02d}:{elapsed_secs:02d}.{elapsed_milliseconds:03d}",
            "num_speakers": num_speakers,
            "num_segments": num_segments,
            "processing_speed": processing_speed,
            "audio_duration_seconds": audio_duration,
        }
        
        self._write_log(f"{'='*60}")
        self._write_log(f"Speaker diarization work completed!")
        self._write_log(f"{'='*60}")
        self._write_log(f"Processing time: {elapsed_hours:02d}:{elapsed_minutes:02d}:{elapsed_secs:02d}.{elapsed_milliseconds:03d} ({elapsed_time:.2f} seconds)")
        self._write_log(f"Number of speakers detected: {num_speakers}")
        self._write_log(f"Number of segments: {num_segments}")
        self._write_log(f"Processing speed: {processing_speed:.2f}x (real-time ratio: {elapsed_time/audio_duration:.2f}x)")
    
    def log_segments(self, segments):
        """Log speaker diarization segments"""
        self._write_log(f"\n--- Speaker Diarization Results ---")
        for turn, _, speaker in segments:
            self._write_log(f"Time: {turn.start:04.1f}s ~ {turn.end:04.1f}s | Speaker: {speaker}")
    
    def log_error(self, error_message, traceback_str=None):
        """Log errors"""
        error_entry = {
            "timestamp": datetime.now().isoformat(),
            "message": str(error_message),
            "traceback": traceback_str,
        }
        self.log_data["errors"].append(error_entry)
        
        self._write_log(f"\n[Error] {error_message}")
        if traceback_str:
            self._write_log(f"Detailed error:\n{traceback_str}")
    
    def log_info(self, message):
        """Log general information"""
        self._write_log(message)
    
    def finish(self):
        """Finish logging and save JSON file"""
        self.log_data["end_time"] = datetime.now().isoformat()
        
        # Save as JSON file
        with open(self.json_file, "w", encoding="utf-8") as f:
            json.dump(self.log_data, f, ensure_ascii=False, indent=2)
        
        self._write_log(f"\n{'='*60}")
        self._write_log(f"Work completed: {self.log_data['end_time']}")
        self._write_log(f"Log file: {self.log_file}")
        self._write_log(f"JSON file: {self.json_file}")
        self._write_log(f"{'='*60}")
        
        return self.log_file, self.json_file
