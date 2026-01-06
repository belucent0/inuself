"""긴 오디오 파일을 청킹하여 ASR 처리하는 모듈."""
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import librosa
import soundfile as sf

from .whisper_utils import run_asr_transcription


@dataclass
class ChunkInfo:
    """오디오 청크 정보."""
    chunk_index: int
    start_time: float
    end_time: float
    audio_path: str | Path


def split_audio_into_chunks(
    audio_path: str | Path,
    chunk_duration_seconds: float,
    overlap_seconds: float,
    temp_dir: Path | None = None,
) -> list[ChunkInfo]:
    """
    오디오 파일을 청크로 분할 (오버랩 포함).
    
    Args:
        audio_path: 원본 오디오 파일 경로
        chunk_duration_seconds: 각 청크의 길이 (초)
        overlap_seconds: 청크 간 오버랩 길이 (초)
        temp_dir: 임시 파일 저장 디렉토리 (None이면 시스템 임시 디렉토리 사용)
    
    Returns:
        청크 정보 리스트
    """
    print(f"[Chunking] Loading audio file: {audio_path}")
    waveform, sample_rate = librosa.load(str(audio_path), sr=16000)
    audio_duration = len(waveform) / sample_rate
    
    print(f"[Chunking] Audio duration: {audio_duration:.2f} seconds")
    print(f"[Chunking] Chunk size: {chunk_duration_seconds:.2f}s, Overlap: {overlap_seconds:.2f}s")
    
    chunks = []
    current_start = 0.0
    chunk_index = 0
    
    if temp_dir is None:
        temp_dir = Path(tempfile.gettempdir())
    else:
        temp_dir = Path(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
    
    while current_start < audio_duration:
        # 청크의 끝 시간 계산 (오버랩 고려)
        chunk_end = min(current_start + chunk_duration_seconds, audio_duration)
        
        # 실제로 처리할 구간 (오버랩 포함)
        actual_start = max(0.0, current_start)
        actual_end = min(audio_duration, chunk_end)
        
        # 청크 길이 확인
        chunk_length = actual_end - actual_start
        
        # 무한 루프 방지: 청크가 너무 작거나 이미 끝에 도달했으면 종료
        if chunk_length < overlap_seconds * 0.5:  # 오버랩의 절반보다 작으면 의미 없음
            print(f"[Chunking] Stopping: chunk length ({chunk_length:.2f}s) is too small")
            break
        
        # 이전 청크와 동일한 구간인지 확인 (무한 루프 방지)
        if chunks and chunks[-1].start_time == actual_start and chunks[-1].end_time == actual_end:
            print(f"[Chunking] Stopping: duplicate chunk detected (same as previous)")
            break
        
        # 샘플 인덱스 계산
        start_sample = int(actual_start * sample_rate)
        end_sample = int(actual_end * sample_rate)
        
        # 청크 오디오 추출
        chunk_waveform = waveform[start_sample:end_sample]
        
        # 임시 WAV 파일로 저장
        chunk_filename = temp_dir / f"chunk_{chunk_index:04d}.wav"
        sf.write(str(chunk_filename), chunk_waveform, sample_rate)
        
        chunks.append(ChunkInfo(
            chunk_index=chunk_index,
            start_time=actual_start,
            end_time=actual_end,
            audio_path=chunk_filename,
        ))
        
        print(f"[Chunking] Chunk {chunk_index}: {actual_start:.2f}s - {actual_end:.2f}s ({chunk_length:.2f}s)")
        
        # 마지막 청크에 도달했는지 확인 (오디오 끝에 도달)
        if chunk_end >= audio_duration:
            print(f"[Chunking] Reached end of audio at {chunk_end:.2f}s")
            break
        
        # 다음 청크 시작 위치 (오버랩 제외)
        next_start = chunk_end - overlap_seconds
        
        # 무한 루프 방지: 진행이 없으면 종료
        if next_start <= current_start:
            print(f"[Chunking] Stopping: no progress (next_start={next_start:.2f}s <= current_start={current_start:.2f}s)")
            break
        
        current_start = next_start
        chunk_index += 1
        
        # 안전장치: 최대 청크 수 제한 (합리적인 값으로 설정)
        if chunk_index > 100:
            print(f"[Chunking] Warning: Too many chunks ({chunk_index}), stopping")
            break
    
    print(f"[Chunking] Created {len(chunks)} chunks")
    return chunks


def extract_prompt_text(
    asr_result: dict[str, Any],
    max_chars: int = 200,
) -> str:
    """
    ASR 결과에서 프롬프트로 사용할 텍스트 추출 (마지막 부분).
    
    Args:
        asr_result: ASR 결과 딕셔너리
        max_chars: 최대 문자 수
    
    Returns:
        프롬프트 텍스트
    """
    segments = asr_result.get("segments", [])
    if not segments:
        return ""
    
    # 마지막 세그먼트들에서 텍스트 추출
    prompt_texts = []
    total_chars = 0
    
    # 뒤에서부터 세그먼트를 가져와서 max_chars까지 채움
    for seg in reversed(segments):
        text = seg.get("text", "").strip()
        if not text:
            continue
        
        if total_chars + len(text) > max_chars:
            # 남은 공간만큼만 추가
            remaining = max_chars - total_chars
            if remaining > 0:
                prompt_texts.insert(0, text[:remaining])
            break
        
        prompt_texts.insert(0, text)
        total_chars += len(text) + 1  # +1 for space
    
    prompt = " ".join(prompt_texts)
    return prompt.strip()


def run_chunked_asr(
    audio_path: str | Path,
    model_size: str,
    chunk_duration_seconds: float,
    overlap_seconds: float,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    """
    오디오를 청크로 분할하여 순차적으로 ASR 실행 (프롬프트 문맥 주입).
    
    Args:
        audio_path: 원본 오디오 파일 경로
        model_size: Whisper 모델 크기
        chunk_duration_seconds: 각 청크의 길이 (초)
        overlap_seconds: 청크 간 오버랩 길이 (초)
        project_root: 프로젝트 루트 경로
    
    Returns:
        각 청크의 ASR 결과 리스트
    """
    print(f"\n{'='*60}")
    print("[Chunked ASR] Starting chunked ASR processing")
    print(f"{'='*60}")
    
    # 오디오를 청크로 분할
    chunks = split_audio_into_chunks(
        audio_path=audio_path,
        chunk_duration_seconds=chunk_duration_seconds,
        overlap_seconds=overlap_seconds,
    )
    
    if not chunks:
        raise ValueError("No chunks created from audio file")
    
    chunk_results = []
    previous_prompt = None
    
    for i, chunk in enumerate(chunks):
        print(f"\n[Chunked ASR] Processing chunk {i+1}/{len(chunks)}")
        print(f"  Time range: {chunk.start_time:.2f}s - {chunk.end_time:.2f}s")
        
        # ASR 실행 (프롬프트 포함)
        asr_result, model_load_time, transcribe_time = run_asr_transcription(
            audio_path=chunk.audio_path,
            model_size=model_size,
            project_root=project_root,
            part_label=f"Chunk {i+1}/{len(chunks)}",
            time_range=(chunk.start_time, chunk.end_time),
            prompt=previous_prompt,
        )
        
        # 타임스탬프를 원본 오디오 기준으로 조정
        adjusted_result = adjust_timestamps(asr_result, chunk.start_time)
        chunk_results.append(adjusted_result)
        
        # 다음 청크를 위한 프롬프트 추출
        previous_prompt = extract_prompt_text(adjusted_result, max_chars=200)
        if previous_prompt:
            print(f"[Chunked ASR] Extracted prompt for next chunk: {previous_prompt[:100]}...")
        
        # 임시 청크 파일 삭제
        try:
            Path(chunk.audio_path).unlink(missing_ok=True)
        except Exception as e:
            print(f"[Chunked ASR] Warning: Failed to delete chunk file {chunk.audio_path}: {e}")
    
    print(f"\n[Chunked ASR] Completed processing {len(chunks)} chunks")
    return chunk_results


def adjust_timestamps(
    asr_result: dict[str, Any],
    offset_seconds: float,
) -> dict[str, Any]:
    """
    ASR 결과의 타임스탬프를 오프셋만큼 조정.
    
    Args:
        asr_result: ASR 결과 딕셔너리
        offset_seconds: 타임스탬프 오프셋 (초)
    
    Returns:
        타임스탬프가 조정된 ASR 결과
    """
    adjusted = {
        "text": asr_result.get("text", ""),
        "language": asr_result.get("language", "ko"),
        "segments": [],
    }
    
    for seg in asr_result.get("segments", []):
        adjusted_seg = seg.copy()
        adjusted_seg["start"] = seg["start"] + offset_seconds
        adjusted_seg["end"] = seg["end"] + offset_seconds
        adjusted["segments"].append(adjusted_seg)
    
    return adjusted


def merge_asr_results(
    chunk_results: list[dict[str, Any]],
    overlap_seconds: float,
    chunk_duration_seconds: float,
) -> dict[str, Any]:
    """
    여러 청크의 ASR 결과를 병합 (오버랩 구간 중복 제거).
    
    Args:
        chunk_results: 각 청크의 ASR 결과 리스트
        overlap_seconds: 오버랩 길이 (초)
        chunk_duration_seconds: 각 청크의 길이 (초)
    
    Returns:
        병합된 ASR 결과
    """
    if not chunk_results:
        return {
            "text": "",
            "language": "ko",
            "segments": [],
        }
    
    if len(chunk_results) == 1:
        return chunk_results[0]
    
    print(f"\n[Merging] Merging {len(chunk_results)} chunk results...")
    
    # 모든 세그먼트 수집
    all_segments = []
    for chunk_result in chunk_results:
        all_segments.extend(chunk_result.get("segments", []))
    
    # 타임스탬프 기준으로 정렬
    all_segments.sort(key=lambda x: x["start"])
    
    # 오버랩이 0이면 모든 세그먼트를 그대로 사용 (필터링 불필요)
    if overlap_seconds == 0:
        print(f"[Merging] No overlap, using all {len(all_segments)} segments from all chunks")
        merged_segments = all_segments
    else:
        # 오버랩 구간 처리
        # 각 청크에서 오버랩 구간의 세그먼트를 제거
        merged_segments = []
        
        # 각 청크의 시작 시간 계산 (오버랩 고려)
        chunk_start_times = []
        for i, chunk_result in enumerate(chunk_results):
            chunk_segments = chunk_result.get("segments", [])
            if chunk_segments:
                chunk_start_times.append(chunk_segments[0]["start"])
            else:
                # 세그먼트가 없으면 이전 청크의 마지막 시간 + 오버랩으로 추정
                if i > 0 and chunk_start_times:
                    chunk_start_times.append(chunk_start_times[-1] + chunk_duration_seconds - overlap_seconds)
                else:
                    chunk_start_times.append(0.0)
        
        # 각 청크별로 세그먼트 필터링
        for i, chunk_result in enumerate(chunk_results):
            chunk_segments = chunk_result.get("segments", [])
            
            if i == 0:
                # 첫 번째 청크: 전체 사용
                merged_segments.extend(chunk_segments)
            else:
                # 두 번째 청크 이후: 오버랩 구간 제거
                # 이전 청크의 끝 시간을 기준으로 오버랩 구간 계산
                prev_chunk_segments = chunk_results[i-1].get("segments", [])
                if prev_chunk_segments:
                    # 이전 청크의 마지막 세그먼트 끝 시간
                    prev_chunk_end = prev_chunk_segments[-1]["end"]
                    # 오버랩 구간: (prev_chunk_end - overlap_seconds) ~ prev_chunk_end
                    # 이전 청크에서 이미 처리된 구간이므로, 현재 청크에서는 prev_chunk_end 이후만 사용
                    overlap_cutoff = prev_chunk_end
                else:
                    # 이전 청크에 세그먼트가 없으면 청크 시작 시간 기준
                    # 청크 시작 시간은 오버랩을 고려한 실제 시작 시간
                    overlap_cutoff = chunk_start_times[i] + overlap_seconds if chunk_start_times else 0.0
                
                # 오버랩 구간 이후의 세그먼트만 추가
                # 이전 청크의 끝 시간 이후 세그먼트만 사용 (오버랩 구간 제외)
                for seg in chunk_segments:
                    # 세그먼트의 시작점이 이전 청크의 끝 시간 이후인지 확인
                    if seg["start"] >= overlap_cutoff:
                        merged_segments.append(seg)
                    else:
                        print(f"[Merging] Skipping overlap segment from chunk {i}: {seg['start']:.2f}s - {seg['end']:.2f}s (overlap cutoff: {overlap_cutoff:.2f}s)")
        
        # 최종 정렬
        merged_segments.sort(key=lambda x: x["start"])
    
    # 텍스트 재구성
    all_texts = [seg["text"] for seg in merged_segments]
    merged_text = " ".join(all_texts)
    
    # 언어는 첫 번째 청크의 언어 사용
    merged_language = chunk_results[0].get("language", "ko")
    
    print(f"[Merging] Merged {len(all_segments)} segments into {len(merged_segments)} segments")
    
    return {
        "text": merged_text,
        "language": merged_language,
        "segments": merged_segments,
    }

