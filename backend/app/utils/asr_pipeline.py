import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from enum import Enum

import librosa
import soundfile as sf
import torch
from pyannote.audio import Pipeline as DiarizationPipeline


WHISPER_MODEL_MAP = {
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

ASR_OVERLAP_SECONDS = 5.0


@dataclass
class PipelineLog:
    event: str
    timestamp: float
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    transcription: dict[str, Any]
    segments: list[dict[str, Any]]
    speaker_stats: dict[str, Any]
    diarization_segments: list[dict[str, Any]]
    duration_seconds: float
    logs: list[PipelineLog]


class ProcessingMode(str, Enum):
    CASE1 = "case1"
    CASE2 = "case2"
    CASE3 = "case3"
    CASE4 = "case4"


def _log(logs: list[PipelineLog], event: str, **data: Any) -> None:
    logs.append(PipelineLog(event=event, timestamp=time.time(), data=data))


class AsrPipelineRunner:
    def __init__(
        self,
        audio_path: Path,
        *,
        model_size: str,
        processing_mode: ProcessingMode,
        num_asr_chunks: int,
        temp_dir: Path,
    ) -> None:
        self.audio_path = audio_path
        self.model_size = model_size
        self.processing_mode = processing_mode
        self.num_asr_chunks = max(1, num_asr_chunks)
        self.temp_dir = temp_dir
        self.logs: list[PipelineLog] = []

        self.waveform, self.sample_rate = librosa.load(str(self.audio_path), sr=16000)
        self.audio_duration = len(self.waveform) / self.sample_rate

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        _log(self.logs, "device", device=self.device)

    def run(self) -> PipelineResult:
        asr_result, diarization, stats = self._run_mode()
        merged_segments = self._assign_speakers(asr_result, diarization)
        speaker_stats = self._calculate_speaker_stats(merged_segments)
        diarization_segments = self._serialize_diarization(diarization)

        transcription_payload = {
            "text": asr_result["text"],
            "language": asr_result.get("language", "ko"),
            "segments": merged_segments,
            "stats": {
                **stats,
            },
        }

        return PipelineResult(
            transcription=transcription_payload,
            segments=merged_segments,
            speaker_stats=speaker_stats,
            diarization_segments=diarization_segments,
            duration_seconds=self.audio_duration,
            logs=self.logs,
        )

    def _run_diarization(self):
        _log(self.logs, "diarization_start")
        start = time.time()
        pipeline = DiarizationPipeline.from_pretrained("pyannote/speaker-diarization-3.1")
        pipeline.to(torch.device(self.device))

        audio_data = {
            "waveform": torch.from_numpy(self.waveform).unsqueeze(0).to(self.device),
            "sample_rate": self.sample_rate,
        }

        with torch.inference_mode():
            result = pipeline(audio_data)
        duration = time.time() - start
        _log(self.logs, "diarization_end", duration=duration)
        return result, {"diarization_time": duration}

    def _run_mode(self):
        match self.processing_mode:
            case ProcessingMode.CASE1:
                return self._run_case1()
            case ProcessingMode.CASE2:
                return self._run_case2()
            case ProcessingMode.CASE3:
                return self._run_case3()
            case ProcessingMode.CASE4:
                return self._run_case4()
            case _:
                raise ValueError(f"Unsupported processing mode {self.processing_mode}")

    def _run_case1(self):
        _log(self.logs, "case1_start")
        diarization_result, diar_stats = self._run_diarization()
        asr_result, asr_stats = self._run_full_file_asr()
        stats = {**diar_stats, **asr_stats}
        _log(self.logs, "case1_end", stats=stats)
        return asr_result, diarization_result, stats

    def _run_case2(self):
        _log(self.logs, "case2_start")
        diarization_result, diar_stats = self._run_diarization()
        split_points = find_optimal_split_points(diarization_result, self.audio_duration, self.num_asr_chunks)
        nominal_ranges = build_nominal_ranges(self.audio_duration, split_points)
        chunk_infos = split_audio_into_chunks(
            self.waveform,
            self.sample_rate,
            self.audio_duration,
            str(self.audio_path),
            nominal_ranges,
            str(self.temp_dir),
            ASR_OVERLAP_SECONDS,
        )
        chunked_results, parallel_time, sequential_time = run_parallel_asr_chunks(
            chunk_infos, self.model_size, max_workers=self.num_asr_chunks
        )
        asr_result = merge_chunked_asr_results(chunked_results)
        stats = {
            "asr_parallel_time": parallel_time,
            "asr_sequential_estimate": sequential_time,
            **diar_stats,
        }
        _log(self.logs, "case2_end", stats=stats)
        cleanup_temp_chunks(chunk_infos)
        return asr_result, diarization_result, stats

    def _run_case3(self):
        _log(self.logs, "case3_start")
        chunk_boundaries = [self.audio_duration * i / self.num_asr_chunks for i in range(1, self.num_asr_chunks)]
        nominal_ranges = build_nominal_ranges(self.audio_duration, chunk_boundaries)
        chunk_infos = split_audio_into_chunks(
            self.waveform,
            self.sample_rate,
            self.audio_duration,
            str(self.audio_path),
            nominal_ranges,
            str(self.temp_dir),
            ASR_OVERLAP_SECONDS,
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            diar_future = executor.submit(self._run_diarization)
            asr_future = executor.submit(
                run_parallel_asr_chunks, chunk_infos, self.model_size, self.num_asr_chunks
            )
            diarization_result, diar_stats = diar_future.result()
            chunked_results, parallel_time, sequential_time = asr_future.result()

        asr_result = merge_chunked_asr_results(chunked_results)
        stats = {
            "diarization_parallel_time": diar_stats["diarization_time"],
            "asr_parallel_time": parallel_time,
            "asr_sequential_estimate": sequential_time,
        }
        cleanup_temp_chunks(chunk_infos)

        _log(self.logs, "case3_end", stats=stats)
        return asr_result, diarization_result, stats

    def _run_case4(self):
        _log(self.logs, "case4_start")
        with ThreadPoolExecutor(max_workers=2) as executor:
            diar_future = executor.submit(self._run_diarization)
            asr_future = executor.submit(self._run_full_file_asr)
            diarization_result, diar_stats = diar_future.result()
            asr_result, asr_stats = asr_future.result()

        stats = {**diar_stats, **asr_stats}
        _log(self.logs, "case4_end", stats=stats)
        return asr_result, diarization_result, stats

    def _run_full_file_asr(self):
        start = time.time()
        result = run_asr(
            audio_file=str(self.audio_path),
            model_size=self.model_size,
        )
        duration = time.time() - start
        stats = {"asr_time": duration}
        return result, stats

    def _assign_speakers(self, asr_result, diarization_result):
        diarization = diarization_result
        speaker_segments = [
            {"start": turn.start, "end": turn.end, "speaker": speaker}
            for turn, _, speaker in diarization.itertracks(yield_label=True)
        ]
        merged_segments = []
        for seg in asr_result.get("segments", []):
            best_speaker = match_speaker(seg["start"], seg["end"], speaker_segments)
            merged_segments.append({**seg, "speaker": best_speaker or "UNKNOWN"})
        return merged_segments

    def _calculate_speaker_stats(self, segments):
        stats: dict[str, dict[str, Any]] = {}
        for seg in segments:
            speaker = seg.get("speaker", "UNKNOWN")
            stats.setdefault(speaker, {"duration": 0.0, "count": 0})
            stats[speaker]["duration"] += seg["end"] - seg["start"]
            stats[speaker]["count"] += 1
        return stats

    def _serialize_diarization(self, diarization):
        items = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            items.append(
                {
                    "start": turn.start,
                    "end": turn.end,
                    "speaker": speaker,
                }
            )
        return items


def run_asr_diarization_pipeline(
    audio_path: str | Path,
    *,
    model_size: str = "base",
    processing_mode: str = "case4",
    num_asr_chunks: int = 2,
) -> PipelineResult:
    if model_size not in WHISPER_MODEL_MAP:
        raise ValueError(f"Unsupported model size {model_size}")
    audio_path = Path(audio_path).resolve()
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    mode = ProcessingMode(processing_mode)
    temp_dir = audio_path.parent / "chunks"
    temp_dir.mkdir(exist_ok=True)

    runner = AsrPipelineRunner(
        audio_path=audio_path,
        model_size=model_size,
        processing_mode=mode,
        num_asr_chunks=num_asr_chunks,
        temp_dir=temp_dir,
    )
    return runner.run()


def get_whispercpp_model_path(model_size: str) -> str:
    filename = WHISPER_MODEL_MAP.get(model_size)
    if not filename:
        raise ValueError(f"Unsupported model size {model_size}")

    project_root = Path(__file__).resolve().parents[3]
    project_model = project_root / "src" / "asr" / "models" / filename
    if project_model.exists():
        return str(project_model)

    external_model = Path("C:/whisper-cpp/models") / filename
    if external_model.exists():
        return str(external_model)
    raise FileNotFoundError(f"Model file not found {filename}")


def run_asr(*, audio_file: str, model_size: str) -> dict[str, Any]:
    model_path = get_whispercpp_model_path(model_size)
    whisper_cli = Path("C:/whisper-cpp/build/bin/Release/whisper-cli.exe")
    if not whisper_cli.exists():
        raise FileNotFoundError("whisper-cli.exe not found")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp_file:
        json_output_path = Path(tmp_file.name)

    cmd = [
        str(whisper_cli),
        "-m",
        model_path,
        "-l",
        "ko",
        "--output-json-full",
        "--output-file",
        str(json_output_path.with_suffix("")),
        audio_file,
    ]
    
    # GPU 사용 확인을 위한 환경 변수 설정
    env = os.environ.copy()
    env.setdefault("GGML_VULKAN_DEVICE", "0")  # Vulkan 디바이스 선택
    
    # Windows에서 프로세스 그룹 생성 플래그 설정
    creation_flags = 0
    if sys.platform == "win32":
        import subprocess as sp
        # CREATE_NEW_PROCESS_GROUP: 프로세스 그룹 생성 (종료 시 자식 프로세스까지 종료 가능)
        creation_flags = sp.CREATE_NEW_PROCESS_GROUP
    
    # subprocess.Popen을 사용하여 프로세스 핸들 추적
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creation_flags if sys.platform == "win32" else 0,
    )
    
    # 전역 리스트에 추가 (종료 시 정리용)
    global _active_child_processes
    _active_child_processes.append(proc)
    
    try:
        # 프로세스 완료 대기
        stdout, stderr = proc.communicate()
        returncode = proc.returncode
    finally:
        # 완료된 프로세스는 리스트에서 제거
        if proc in _active_child_processes:
            _active_child_processes.remove(proc)
    
    # subprocess.run()과 동일한 형식으로 결과 생성
    result = subprocess.CompletedProcess(
        cmd,
        returncode,
        stdout,
        stderr,
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"whisper-cli failed: {result.stderr}")

    final_json = json_output_path.with_suffix(".json")
    if not final_json.exists():
        raise FileNotFoundError("ASR output missing")

    with final_json.open("rb") as f:
        content = f.read()
    if content.startswith(b"\xef\xbb\xbf"):
        content = content[3:]
    json_data = json.loads(content.decode("utf-8", errors="replace"))
    try:
        final_json.unlink()
    except OSError:
        pass
    return parse_whispercpp_json(json_data)


def parse_whispercpp_json(json_data: dict[str, Any]) -> dict[str, Any]:
    result = {
        "text": "",
        "language": json_data.get("result", {}).get("language", "ko"),
        "segments": [],
    }
    transcription = json_data.get("transcription", [])
    all_texts = []
    for i, seg in enumerate(transcription):
        start = _timestamp_to_seconds(seg.get("timestamps", {}).get("from", "00:00:00,000"))
        end = _timestamp_to_seconds(seg.get("timestamps", {}).get("to", "00:00:00,000"))
        text = seg.get("text", "").strip()
        all_texts.append(text)
        result["segments"].append(
            {
                "id": i,
                "start": start,
                "end": end,
                "text": text,
            }
        )
    result["text"] = " ".join(all_texts)
    return result


def _timestamp_to_seconds(ts: str) -> float:
    time_part, _, millis = ts.partition(",")
    h, m, s = [int(part) for part in time_part.split(":")]
    total = h * 3600 + m * 60 + s
    if millis:
        total += int(millis) / 1000
    return total


def find_optimal_split_points(diarization_result, audio_duration: float, num_chunks: int) -> list[float]:
    if num_chunks <= 1:
        return []
    segments = _extract_speaker_segments(diarization_result)
    transitions = _compute_speaker_transitions(segments)
    search_window = max(5.0, audio_duration * 0.1)
    boundaries = []
    for i in range(1, num_chunks):
        target = audio_duration * i / num_chunks
        candidates = [t for t in transitions if abs(t["point"] - target) <= search_window]
        if candidates:
            candidates.sort(key=lambda x: (abs(x["point"] - target), -x["gap"]))
            selected = candidates[0]["point"]
        else:
            selected = target
        boundaries.append(max(0.0, min(audio_duration, selected)))
    unique = []
    for point in sorted(boundaries):
        if unique and abs(point - unique[-1]) < 1e-3:
            continue
        unique.append(point)
    return unique


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


def build_nominal_ranges(audio_duration: float, boundary_points: list[float]) -> list[tuple[float, float]]:
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
    sample_rate: int,
    audio_duration: float,
    audio_file_path: str,
    nominal_ranges: list[tuple[float, float]],
    output_dir: str,
    overlap_seconds: float = 5.0,
):
    audio_name = Path(audio_file_path).stem
    chunk_infos = []
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


def cleanup_temp_chunks(chunk_infos):
    for chunk in chunk_infos:
        try:
            os.unlink(chunk["path"])
        except OSError:
            pass


def run_parallel_asr_chunks(chunk_infos, model_size: str, max_workers: int | None = None):
    if not chunk_infos:
        raise ValueError("No chunk infos provided")
    worker_count = max_workers or len(chunk_infos)
    start_time = time.time()
    futures = {}
    results = {}
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for chunk in chunk_infos:
            label = f"Part {chunk['index'] + 1}"
            future = executor.submit(
                run_asr, audio_file=chunk["path"], model_size=model_size
            )
            futures[future] = chunk
        for future in as_completed(futures):
            chunk = futures[future]
            asr_result = future.result()
            results[chunk["index"]] = {"chunk": chunk, "result": asr_result}

    ordered = [results[i] for i in sorted(results.keys())]
    parallel_time = time.time() - start_time
    sequential_time = sum(
        # Run time approximation is not precise; use placeholder
        chunk["chunk_end"] - chunk["chunk_start"] for chunk in chunk_infos
    )
    return ordered, parallel_time, sequential_time


def merge_chunked_asr_results(chunked_results):
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
            adjusted = seg.copy()
            adjusted["start"] = keep_start
            adjusted["end"] = keep_end
            merged_segments.append(adjusted)
    merged_segments.sort(key=lambda x: x["start"])
    for idx, seg in enumerate(merged_segments):
        seg["id"] = idx
    text = " ".join(seg.get("text", "").strip() for seg in merged_segments)
    return {"text": text, "language": language, "segments": merged_segments}


def match_speaker(seg_start: float, seg_end: float, speaker_segments: list[dict[str, Any]]) -> str | None:
    best_speaker = None
    max_overlap = 0.0
    for spk in speaker_segments:
        overlap_start = max(seg_start, spk["start"])
        overlap_end = min(seg_end, spk["end"])
        overlap = max(0.0, overlap_end - overlap_start)
        if overlap > max_overlap:
            max_overlap = overlap
            best_speaker = spk["speaker"]
    if best_speaker is None:
        mid = (seg_start + seg_end) / 2
        for spk in speaker_segments:
            if spk["start"] <= mid <= spk["end"]:
                return spk["speaker"]
    return best_speaker

