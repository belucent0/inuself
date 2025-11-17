# src/diarization/test_asr_with_diarization.py
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
import soundfile as sf
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
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # src/pipeline -> src -> project_root
sys.path.insert(0, project_root)

# Windows console encoding configuration
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 결과 저장 경로 설정 (로그 파일 시작 전에 미리 설정)
# pipeline 전용 logs 폴더 사용
script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
pipeline_dir = script_dir  # src/pipeline
output_dir = pipeline_dir / "logs"
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
audio_file = sys.argv[2] if len(sys.argv) > 2 else os.path.join(project_root, "media", "wav", "sample.wav")
audio_file = os.path.abspath(audio_file)

# 처리 모드 선택 (명령줄 인자 또는 기본값)
# case1: 화자분리 → ASR(전체 파일) 순차 처리
# case2: 화자분리 → ASR(분할 병렬) 순차 처리
# case3: 화자분리와 ASR 모두 분할 병렬 처리
# case4: 화자분리와 ASR(전체 파일) 병렬 처리
processing_mode = sys.argv[3] if len(sys.argv) > 3 else "case1"
if processing_mode not in ["case1", "case2", "case3", "case4"]:
    print(f"Error: Invalid processing mode '{processing_mode}'.")
    print(f"Available modes: case1, case2, case3, case4")
    print(f"  case1: Diarization → ASR (full file) sequential")
    print(f"  case2: Diarization → ASR (split parallel) sequential")
    print(f"  case3: Diarization and ASR (both split) parallel")
    print(f"  case4: Diarization and ASR (full file) parallel")
    sys.exit(1)

# ASR 병렬 조각 수 (기본 2개)
num_asr_chunks = int(sys.argv[4]) if len(sys.argv) > 4 else 2
if num_asr_chunks < 1:
    print("Error: Number of ASR chunks must be at least 1.")
    sys.exit(1)

ASR_OVERLAP_SECONDS = 5.0

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
print(f"Processing mode: {processing_mode}")
print(f"ASR parallel chunks: {num_asr_chunks} (overlap {ASR_OVERLAP_SECONDS:.1f}s)")
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
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # src/pipeline -> src -> project_root
    project_root = os.path.dirname(os.path.dirname(script_dir))
    asr_dir = os.path.join(project_root, "src", "asr")
    project_model_path = os.path.join(asr_dir, "models", model_filename)
    if os.path.exists(project_model_path):
        return project_model_path
    
    # 우선순위 2: C:/whisper-cpp/models/
    external_model_path = os.path.join("C:/whisper-cpp/models", model_filename)
    if os.path.exists(external_model_path):
        return external_model_path
    
    raise FileNotFoundError(f"Model file not found: {model_filename}")

def _extract_speaker_segments(diarization_result):
    segments = []
    for turn, _, speaker in diarization_result.itertracks(yield_label=True):
        segments.append((turn.start, turn.end, speaker))
    segments.sort(key=lambda x: x[0])
    return segments


def _compute_speaker_transitions(segments):
    transitions = []
    for i in range(len(segments) - 1):
        current_speaker = segments[i][2]
        next_speaker = segments[i + 1][2]
        if current_speaker == next_speaker:
            continue
        transition_start = segments[i][1]
        transition_end = segments[i + 1][0]
        center = (transition_start + transition_end) / 2
        gap = max(0.0, transition_end - transition_start)
        transitions.append(
            {
                "point": center,
                "gap": gap,
                "start": transition_start,
                "end": transition_end,
            }
        )
    return transitions


def find_optimal_split_points(diarization_result, audio_duration, num_chunks):
    """
    화자 분리 결과를 기반으로 여러 분할 지점을 계산한다.
    """
    if num_chunks <= 1:
        return []
    
    segments = _extract_speaker_segments(diarization_result)
    transitions = _compute_speaker_transitions(segments)
    search_window = max(5.0, audio_duration * 0.1)
    boundaries = []
    
    for i in range(1, num_chunks):
        target = audio_duration * i / num_chunks
        candidates = [
            t for t in transitions if abs(t["point"] - target) <= search_window
        ]
        if candidates:
            candidates.sort(key=lambda x: (abs(x["point"] - target), -x["gap"]))
            selected = candidates[0]["point"]
            print(
                f"[Split] Using speaker transition near {target:.2f}s -> {selected:.2f}s "
                f"(gap {candidates[0]['gap']:.2f}s)"
            )
        else:
            selected = target
            print(
                f"[Split] No transition near {target:.2f}s, using target as boundary."
            )
        boundaries.append(max(0.0, min(audio_duration, selected)))
    
    # 정렬 및 중복 제거
    unique_boundaries = []
    for point in sorted(boundaries):
        if unique_boundaries and abs(point - unique_boundaries[-1]) < 1e-3:
            continue
        unique_boundaries.append(point)
    return unique_boundaries


def build_nominal_ranges(audio_duration, boundary_points):
    """
    분할 지점 리스트를 기반으로 (start, end) 구간 리스트 생성
    """
    ranges = []
    prev = 0.0
    for boundary in boundary_points:
        boundary = max(prev + 1e-3, min(audio_duration - 1e-3, boundary))
        ranges.append((prev, boundary))
        prev = boundary
    ranges.append((prev, audio_duration))
    return ranges


def split_audio_into_chunks(
    waveform_data,
    sample_rate,
    audio_duration,
    audio_file_path,
    nominal_ranges,
    output_dir,
    overlap_seconds=5.0,
):
    """
    지정된 구간을 기반으로 오디오를 여러 개로 분할한다.
    """
    audio_name = Path(audio_file_path).stem
    chunk_infos = []
    sequential_asr_time = 0.0
    half_overlap = overlap_seconds / 2
    
    for idx, (nominal_start, nominal_end) in enumerate(nominal_ranges):
        chunk_start = nominal_start if idx == 0 else max(0.0, nominal_start - half_overlap)
        chunk_end = nominal_end if idx == len(nominal_ranges) - 1 else min(
            audio_duration, nominal_end + half_overlap
        )
        start_sample = int(chunk_start * sample_rate)
        end_sample = int(chunk_end * sample_rate)
        chunk_waveform = waveform_data[start_sample:end_sample]
        chunk_path = os.path.join(output_dir, f"{audio_name}_part{idx + 1}.wav")
        sf.write(chunk_path, chunk_waveform, sample_rate)
        
        print(
            f"[Split] Part {idx + 1}: chunk {chunk_start:.2f}s - {chunk_end:.2f}s "
            f"(nominal {nominal_start:.2f}s - {nominal_end:.2f}s)"
        )
        
        chunk_infos.append(
            {
                "index": idx,
                "path": chunk_path,
                "chunk_start": chunk_start,
                "chunk_end": chunk_end,
                "nominal_start": nominal_start,
                "nominal_end": nominal_end,
            }
        )
    
    return chunk_infos

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

def run_asr(audio_path=None, part_label=None, time_range=None):
    """
    ASR 전사 실행 (동기 처리)
    
    Args:
        audio_path: 오디오 파일 경로
        part_label: 파트 식별자 (예: "Part 1", "Part 2")
        time_range: 시간 구간 튜플 (start, end) 또는 None
    """
    if audio_path is None:
        audio_path = audio_file
    
    # 파트 정보가 있으면 로그에 표시
    if part_label and time_range:
        time_info = f" [{time_range[0]:.2f}s - {time_range[1]:.2f}s]"
    elif part_label:
        time_info = ""
    else:
        time_info = ""
        part_label = ""
    
    part_prefix = f"[ASR {part_label}]" if part_label else "[ASR]"
    print(f"{part_prefix} Using whisper-cli.exe with model: {model_size}{time_info}")
    
    # 모델 경로 찾기
    model_load_start = time.time()
    try:
        model_path = get_whispercpp_model_path(model_size)
        print(f"{part_prefix} Model found: {model_path}")
    except (ValueError, FileNotFoundError) as e:
        print(f"{part_prefix} Error: {e}")
        raise
    
    model_load_time = time.time() - model_load_start
    
    # whisper-cli.exe 경로
    whisper_cli = "C:/whisper-cpp/build/bin/Release/whisper-cli.exe"
    if not os.path.exists(whisper_cli):
        raise FileNotFoundError(f"whisper-cli.exe not found: {whisper_cli}")
    
    if time_range:
        print(f"{part_prefix} Starting transcription for time range {time_range[0]:.2f}s - {time_range[1]:.2f}s...")
    else:
        print(f"{part_prefix} Starting transcription...")
    
    # 임시 JSON 출력 파일 생성
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, dir=os.path.dirname(os.path.abspath(__file__))) as tmp_file:
        json_output_path = tmp_file.name
    
    transcribe_start = time.time()
    
    # whisper-cli.exe 실행 (동기 - subprocess.run)
    cmd = [
        whisper_cli,
        "-m", model_path,
        "-l", "ko",
        "--output-json-full",
        "--output-file", json_output_path.replace('.json', ''),
        audio_path
    ]
    
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        # 출력 로그
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
        raise

def run_parallel_asr_chunks(chunk_infos, max_workers=None):
    """
    여러 오디오 조각을 병렬로 ASR 처리한다.
    """
    if not chunk_infos:
        raise ValueError("chunk_infos must not be empty.")
    
    worker_count = max_workers or len(chunk_infos)
    worker_count = max(1, min(worker_count, len(chunk_infos)))
    
    print(
        f"[Parallel ASR] Starting transcription of {len(chunk_infos)} chunks "
        f"with {worker_count} workers..."
    )
    
    parallel_start = time.time()
    futures = {}
    results = {}
    
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for chunk in chunk_infos:
            label = f"Part {chunk['index'] + 1}"
            time_range = (chunk["chunk_start"], chunk["chunk_end"])
            future = executor.submit(run_asr, chunk["path"], label, time_range)
            futures[future] = chunk
        
        for future in as_completed(futures):
            chunk = futures[future]
            asr_result, load_time, transcribe_time = future.result()
            results[chunk["index"]] = {
                "chunk": chunk,
                "result": asr_result,
                "load_time": load_time,
                "transcribe_time": transcribe_time,
            }
    
    ordered_results = [results[i] for i in sorted(results.keys())]
    parallel_time = time.time() - parallel_start
    sequential_time = sum(
        entry["load_time"] + entry["transcribe_time"] for entry in ordered_results
    )
    
    print(f"[Parallel ASR] Completed in {parallel_time:.2f}s "
          f"(sequential estimate {sequential_time:.2f}s, "
          f"time saved {sequential_time - parallel_time:.2f}s)")
    
    return ordered_results, parallel_time, sequential_time


def merge_chunked_asr_results(chunked_results):
    """
    여러 ASR 조각 결과를 하나로 병합한다.
    """
    if not chunked_results:
        return {"text": "", "language": "ko", "segments": []}
    
    merged_segments = []
    language = chunked_results[0]["result"].get("language", "ko")
    
    for entry in chunked_results:
        chunk = entry["chunk"]
        result = entry["result"]
        chunk_offset = chunk["chunk_start"]
        nominal_start = chunk["nominal_start"]
        nominal_end = chunk["nominal_end"]
        
        for seg in result.get("segments", []):
            global_start = seg["start"] + chunk_offset
            global_end = seg["end"] + chunk_offset
            keep_start = max(global_start, nominal_start)
            keep_end = min(global_end, nominal_end)
            if keep_end <= keep_start:
                continue
            adjusted_seg = seg.copy()
            adjusted_seg["start"] = keep_start
            adjusted_seg["end"] = keep_end
            merged_segments.append(adjusted_seg)
    
    merged_segments.sort(key=lambda x: x["start"])
    all_texts = []
    for idx, seg in enumerate(merged_segments):
        seg["id"] = idx
        all_texts.append(seg.get("text", "").strip())
    
    merged_result = {
        "text": " ".join([t for t in all_texts if t]),
        "language": language,
        "segments": merged_segments,
    }
    print(f"[Merge] Combined {len(chunked_results)} chunk results into "
          f"{len(merged_segments)} segments")
    return merged_result

def run_diarization():
    """화자 분리 실행 (전체 오디오 파일 처리)"""
    print(f"[Diarization] Starting speaker diarization for entire audio file...")
    print(f"[Diarization] Processing time range: 0.00s - {audio_duration:.2f}s ({audio_duration:.2f}s)")
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

# ==========================================
# Case 1: 화자분리 → ASR(전체 파일) 순차 처리
# ==========================================
def run_case1_sequential_full_asr():
    """
    Case 1: 화자분리 → ASR(전체 파일) 순차 처리
    
    Returns:
        (diarization, diarization_load_time, diarization_time,
         asr_result, model_load_time, transcribe_time, execution_time)
    """
    print(f"\n{'='*60}")
    print("[Case 1] Sequential Processing: Diarization → ASR (Full File)")
    print(f"{'='*60}")
    print(f"[Note] Diarization completes first, then ASR processes entire file")
    print(f"       - No file splitting")
    print(f"       - ASR uses full context")
    
    case_start = time.time()
    
    # 1. 화자분리 실행
    print(f"\n[Step 1] Starting speaker diarization...")
    diarization, diarization_load_time, diarization_time = run_diarization()
    
    # 2. ASR 실행 (전체 파일)
    print(f"\n[Step 2] Starting ASR transcription on entire file...")
    print(f"[ASR] Processing entire audio file (0.00s - {audio_duration:.2f}s)")
    asr_result, model_load_time, transcribe_time = run_asr(audio_file)
    
    execution_time = time.time() - case_start
    
    print(f"\n[Case 1] All tasks completed in {execution_time:.2f} seconds")
    print(f"  - Diarization (load + process): {diarization_load_time + diarization_time:.2f}s")
    print(f"  - ASR (load + transcribe): {model_load_time + transcribe_time:.2f}s")
    print(f"  - Total sequential time: {execution_time:.2f}s")
    
    return diarization, diarization_load_time, diarization_time, asr_result, model_load_time, transcribe_time, execution_time

# ==========================================
# Case 2: 화자분리 → ASR(분할 병렬) 순차 처리
# ==========================================
def run_case2_sequential_split_asr():
    """
    Case 2: 화자분리 → ASR(분할 병렬) 순차 처리
    
    Returns:
        (diarization, diarization_load_time, diarization_time,
         asr_result, model_load_time, transcribe_time, execution_time)
    """
    print(f"\n{'='*60}")
    print("[Case 2] Sequential Processing: Diarization → ASR (Split Parallel)")
    print(f"{'='*60}")
    print(f"[Note] Diarization completes first, then ASR processes split files in parallel")
    print(f"       - File split based on diarization results")
    print(f"       - ASR runs with {num_asr_chunks} parallel chunks")
    
    case_start = time.time()
    
    # 1. 화자분리 실행
    print(f"\n[Step 1] Starting speaker diarization...")
    diarization, diarization_load_time, diarization_time = run_diarization()
    
    # 2. 분할 지점 결정
    print(f"\n[Step 2] Determining optimal split points from diarization results...")
    split_points = find_optimal_split_points(diarization, audio_duration, num_asr_chunks)
    nominal_ranges = build_nominal_ranges(audio_duration, split_points)
    
    # 3. 오디오 파일 분할
    print(f"\n[Step 3] Splitting audio file for parallel ASR processing...")
    temp_dir = pipeline_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    temp_dir = str(temp_dir)
    
    chunk_infos = []
    
    try:
        chunk_infos = split_audio_into_chunks(
            waveform,
            sample_rate,
            audio_duration,
            audio_file,
            nominal_ranges,
            temp_dir,
            ASR_OVERLAP_SECONDS,
        )
        
        # 4. 병렬 ASR 실행
        print(f"\n[Step 4] Starting parallel ASR transcription...")
        chunked_results, parallel_asr_time, sequential_asr_time = run_parallel_asr_chunks(
            chunk_infos, max_workers=num_asr_chunks
        )
        
        # 5. ASR 결과 병합
        print(f"\n[Step 5] Merging ASR results...")
        asr_result = merge_chunked_asr_results(chunked_results)
        
        model_load_time = 0
        transcribe_time = parallel_asr_time
        
    finally:
        # 임시 파일 정리
        print(f"\n[Cleanup] Removing temporary audio files...")
        try:
            for chunk in chunk_infos:
                if os.path.exists(chunk["path"]):
                    os.unlink(chunk["path"])
        except Exception as e:
            print(f"[Cleanup] Warning: Failed to remove some temp files: {e}")
    
    execution_time = time.time() - case_start
    
    print(f"\n[Case 2] All tasks completed in {execution_time:.2f} seconds")
    print(f"  - Diarization (load + process): {diarization_load_time + diarization_time:.2f}s")
    print(f"  - Parallel ASR (load + transcribe): {transcribe_time:.2f}s (sequential {sequential_asr_time:.2f}s)")
    print(f"  - Total sequential time: {execution_time:.2f}s")
    
    return diarization, diarization_load_time, diarization_time, asr_result, model_load_time, transcribe_time, execution_time

# ==========================================
# Case 3: 화자분리와 ASR 모두 분할 병렬 처리
# ==========================================
def run_case3_parallel_split_all():
    """
    Case 3: 화자분리와 ASR 모두 분할 병렬 처리
    
    Returns:
        (diarization, diarization_load_time, diarization_time,
         asr_result, model_load_time, transcribe_time, execution_time)
    """
    print(f"\n{'='*60}")
    print("[Case 3] Parallel Processing: Diarization and ASR (Both Split)")
    print(f"{'='*60}")
    print(f"[Note] Diarization and ASR run simultaneously with file splitting")
    print(f"       - ASR evenly split into {num_asr_chunks} chunks")
    print(f"       - Both tasks run in parallel")
    
    case_start = time.time()
    
    # 오디오 구간을 균등 분할
    chunk_boundaries = [audio_duration * i / num_asr_chunks for i in range(1, num_asr_chunks)]
    nominal_ranges = build_nominal_ranges(audio_duration, chunk_boundaries)
    
    # 오디오 파일 분할
    print(f"\n[Step 1] Splitting audio file into {num_asr_chunks} chunks...")
    temp_dir = pipeline_dir / "temp"
    temp_dir.mkdir(exist_ok=True)
    temp_dir = str(temp_dir)
    
    chunk_infos = []
    sequential_asr_time = 0.0
    
    try:
        chunk_infos = split_audio_into_chunks(
            waveform,
            sample_rate,
            audio_duration,
            audio_file,
            nominal_ranges,
            temp_dir,
            ASR_OVERLAP_SECONDS,
        )
        
        # 화자분리와 ASR을 동시에 실행
        print(f"\n[Step 2] Starting Diarization and Parallel ASR simultaneously...")
        print(f"[Parallel]   - Diarization: Processing entire audio (0.00s - {audio_duration:.2f}s)")
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            # 화자분리 작업 제출
            diarization_future = executor.submit(run_diarization)
            
            # ASR 병렬 작업 제출
            asr_future = executor.submit(
                run_parallel_asr_chunks, chunk_infos, num_asr_chunks
            )
            
            # 두 작업 모두 완료 대기
            print(f"[Parallel] Waiting for both Diarization and ASR to complete...")
            diarization, diarization_load_time, diarization_time = diarization_future.result()
            chunked_results, parallel_asr_time, sequential_asr_time = asr_future.result()
        
        # ASR 결과 병합
        print(f"\n[Step 3] Merging ASR results...")
        asr_result = merge_chunked_asr_results(chunked_results)
        
        model_load_time = 0
        transcribe_time = parallel_asr_time
        
    finally:
        # 임시 파일 정리
        print(f"\n[Cleanup] Removing temporary audio files...")
        try:
            for chunk in chunk_infos:
                if os.path.exists(chunk["path"]):
                    os.unlink(chunk["path"])
        except Exception as e:
            print(f"[Cleanup] Warning: Failed to remove some temp files: {e}")
    
    execution_time = time.time() - case_start
    
    print(f"\n[Case 3] All tasks completed in {execution_time:.2f} seconds")
    print(f"  - Diarization (load + process): {diarization_load_time + diarization_time:.2f}s")
    print(f"  - Parallel ASR (load + transcribe): {transcribe_time:.2f}s (sequential {sequential_asr_time:.2f}s)")
    expected_sequential_time = (diarization_load_time + diarization_time) + sequential_asr_time
    time_saved = expected_sequential_time - execution_time
    print(f"  - Expected sequential time: {expected_sequential_time:.2f}s")
    print(f"  - Time saved: {time_saved:.2f}s ({time_saved/expected_sequential_time*100:.1f}%)")
    
    return diarization, diarization_load_time, diarization_time, asr_result, model_load_time, transcribe_time, execution_time

# ==========================================
# Case 4: 화자분리와 ASR(전체 파일) 병렬 처리
# ==========================================
def run_case4_parallel_full_asr():
    """
    Case 4: 화자분리와 ASR(전체 파일) 병렬 처리
    
    Returns:
        (diarization, diarization_load_time, diarization_time,
         asr_result, model_load_time, transcribe_time, execution_time)
    """
    print(f"\n{'='*60}")
    print("[Case 4] Parallel Processing: Diarization and ASR (Full File)")
    print(f"{'='*60}")
    print(f"[Note] Diarization and ASR run simultaneously")
    print(f"       - ASR processes entire file (no splitting)")
    print(f"       - Both tasks run in parallel")
    print(f"       - ASR uses full context")
    
    case_start = time.time()
    
    # 화자분리와 ASR을 동시에 실행
    print(f"\n[Step 1] Starting Diarization and ASR simultaneously...")
    print(f"[Parallel]   - Diarization: Processing entire audio (0.00s - {audio_duration:.2f}s)")
    print(f"[Parallel]   - ASR: Processing entire audio (0.00s - {audio_duration:.2f}s)")
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        # 화자분리 작업 제출
        diarization_future = executor.submit(run_diarization)
        
        # ASR 작업 제출 (전체 파일)
        asr_future = executor.submit(run_asr, audio_file)
        
        # 두 작업 모두 완료 대기
        print(f"[Parallel] Waiting for both Diarization and ASR to complete...")
        diarization, diarization_load_time, diarization_time = diarization_future.result()
        asr_result, model_load_time, transcribe_time = asr_future.result()
    
    execution_time = time.time() - case_start
    
    print(f"\n[Case 4] All tasks completed in {execution_time:.2f} seconds")
    print(f"  - Diarization (load + process): {diarization_load_time + diarization_time:.2f}s")
    print(f"  - ASR (load + transcribe): {model_load_time + transcribe_time:.2f}s")
    expected_sequential_time = (diarization_load_time + diarization_time) + (model_load_time + transcribe_time)
    time_saved = expected_sequential_time - execution_time
    print(f"  - Expected sequential time: {expected_sequential_time:.2f}s")
    print(f"  - Time saved: {time_saved:.2f}s ({time_saved/expected_sequential_time*100:.1f}%)")
    
    return diarization, diarization_load_time, diarization_time, asr_result, model_load_time, transcribe_time, execution_time

# ==========================================
# 메인 처리 로직: 케이스 선택 및 실행
# ==========================================
print(f"\n{'='*60}")
print(f"Processing Mode: {processing_mode.upper()}")
print(f"{'='*60}")

if processing_mode == "case1":
    diarization, diarization_load_time, diarization_time, asr_result, model_load_time, transcribe_time, execution_time = run_case1_sequential_full_asr()
elif processing_mode == "case2":
    diarization, diarization_load_time, diarization_time, asr_result, model_load_time, transcribe_time, execution_time = run_case2_sequential_split_asr()
elif processing_mode == "case3":
    diarization, diarization_load_time, diarization_time, asr_result, model_load_time, transcribe_time, execution_time = run_case3_parallel_split_all()
elif processing_mode == "case4":
    diarization, diarization_load_time, diarization_time, asr_result, model_load_time, transcribe_time, execution_time = run_case4_parallel_full_asr()
else:
    print(f"Error: Unknown processing mode '{processing_mode}'")
    sys.exit(1)

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
print(f"  - Processing mode: {processing_mode}")
print(f"  - Execution time: {execution_time:.2f}s")
print(f"    * ASR (load + transcribe): {model_load_time + transcribe_time:.2f}s")
print(f"    * Diarization (load + process): {diarization_load_time + diarization_time:.2f}s")
print(f"  - Result merging: {merge_time:.2f}s")
print(f"  - ASR chunks: {num_asr_chunks} (overlap {ASR_OVERLAP_SECONDS:.1f}s)")
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
    "processing_mode": processing_mode,
    "asr_chunk_count": num_asr_chunks,
    "asr_overlap_seconds": ASR_OVERLAP_SECONDS,
    "total_processing_time": total_time,
    "execution_time": execution_time,
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

