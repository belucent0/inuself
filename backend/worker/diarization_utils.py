"""화자분리 유틸리티."""
import time
from typing import Any

from . import rocm_env as _rocm_env  # noqa: F401  # sys.path side-effect

import numpy as np
import torch
from pyannote.audio import Pipeline as DiarizationPipeline

# HuggingFace Hub 호환성 패치: use_auth_token -> token
# pyannote.audio가 사용하는 오래된 API를 최신 API로 변환
try:
    import huggingface_hub
    import functools
    
    # 원본 함수 백업 (이미 패치된 경우를 대비)
    if not hasattr(huggingface_hub, '_original_hf_hub_download'):
        huggingface_hub._original_hf_hub_download = huggingface_hub.hf_hub_download
    
    # use_auth_token을 token으로 변환하는 래퍼
    @functools.wraps(huggingface_hub._original_hf_hub_download)
    def _patched_hf_hub_download(*args, **kwargs):
        if "use_auth_token" in kwargs:
            kwargs["token"] = kwargs.pop("use_auth_token")
        return huggingface_hub._original_hf_hub_download(*args, **kwargs)
    
    # monkey patch 적용
    huggingface_hub.hf_hub_download = _patched_hf_hub_download
    
    # utils 모듈에도 패치 적용 (pyannote.audio가 사용할 수 있음)
    if hasattr(huggingface_hub, 'utils'):
        if hasattr(huggingface_hub.utils, 'hf_hub_download'):
            huggingface_hub.utils.hf_hub_download = _patched_hf_hub_download
except (ImportError, AttributeError):
    # huggingface_hub가 없거나 이미 다른 방식으로 import된 경우 무시
    pass


def run_diarization(
    waveform: Any,
    sample_rate: int,
    device: str = "cuda",
    audio_duration: float | None = None,
    return_embeddings: bool = False,
    return_pipeline: bool = False,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> tuple[Any, float, float, dict[str, Any] | None, Any | None]:
    """
    화자 분리 실행 (전체 오디오 파일 처리).
    
    Args:
        waveform: 오디오 웨이브폼 데이터 (numpy array)
        sample_rate: 샘플레이트
        device: 디바이스 ("cuda" 또는 "cpu")
        audio_duration: 오디오 길이 (초), 로그용
        return_embeddings: embedding 벡터도 반환할지 여부
        return_pipeline: pipeline 객체도 반환할지 여부 (시간대별 임베딩 추출용)
    
    Returns:
        (diarization_result, load_time, process_time, embeddings_dict, pipeline)
        - embeddings_dict: return_embeddings=True일 때만 제공
          {speaker_label: embedding_vector} 형태
        - pipeline: return_pipeline=True일 때만 제공
    """
    if audio_duration:
        print(f"[Diarization] Starting speaker diarization for entire audio file...")
        print(f"[Diarization] Processing time range: 0.00s - {audio_duration:.2f}s ({audio_duration:.2f}s)")
    else:
        print(f"[Diarization] Starting speaker diarization...")
    
    print(f"[Diarization] Loading speaker diarization model...")
    
    diarization_load_start = time.time()
    diarization_pipeline = DiarizationPipeline.from_pretrained("pyannote/speaker-diarization-3.1")
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
        # 화자 수 제약 조건 설정
        pipeline_kwargs = {}
        if num_speakers is not None:
            pipeline_kwargs['num_speakers'] = num_speakers
            print(f"[Diarization] Using fixed number of speakers: {num_speakers}")
        elif min_speakers is not None or max_speakers is not None:
            if min_speakers is not None:
                pipeline_kwargs['min_speakers'] = min_speakers
            if max_speakers is not None:
                pipeline_kwargs['max_speakers'] = max_speakers
            print(f"[Diarization] Using speaker range: min={min_speakers}, max={max_speakers}")
        
        if return_embeddings:
            # embedding도 함께 반환하는 경우
            try:
                # pyannote.audio의 일부 버전에서는 return_embeddings 파라미터 지원
                result, embeddings = diarization_pipeline(audio_data, return_embeddings=True, **pipeline_kwargs)
                # embeddings를 화자별로 매핑
                embeddings_dict = {}
                speaker_labels = list(result.labels())
                if embeddings is not None:
                    # embeddings는 (num_speakers, dimension) 형태일 수 있음
                    if len(embeddings.shape) == 2 and embeddings.shape[0] == len(speaker_labels):
                        for i, speaker in enumerate(speaker_labels):
                            embeddings_dict[speaker] = embeddings[i].tolist() if isinstance(embeddings[i], np.ndarray) else embeddings[i]
                    else:
                        # 다른 형태의 embeddings 처리
                        print(f"[Diarization] Warning: Unexpected embeddings shape: {embeddings.shape}")
                        embeddings_dict = None
                else:
                    embeddings_dict = None
            except TypeError:
                # return_embeddings 파라미터를 지원하지 않는 경우
                print(f"[Diarization] return_embeddings not supported, extracting manually...")
                result = diarization_pipeline(audio_data, **pipeline_kwargs)
                embeddings_dict = extract_speaker_embeddings(
                    diarization_pipeline, audio_data, result
                )
        else:
            result = diarization_pipeline(audio_data, **pipeline_kwargs)
            embeddings_dict = None
    diarization_time = time.time() - diarization_start
    
    print(f"[Diarization] Completed in {diarization_time:.2f} seconds")
    
    if return_pipeline:
        return result, diarization_load_time, diarization_time, embeddings_dict, diarization_pipeline
    else:
        return result, diarization_load_time, diarization_time, embeddings_dict, None


def extract_segment_embeddings(
    pipeline: Any,
    audio_data: dict[str, Any],
    diarization_result: Any,
    min_segment_duration: float = 0.5,
) -> list[dict[str, Any]] | None:
    """
    시간대별 세그먼트 embedding 벡터를 추출합니다.
    
    Args:
        pipeline: pyannote.audio Pipeline 객체
        audio_data: 오디오 데이터 딕셔너리
        diarization_result: 화자 분리 결과 (Annotation 객체)
        min_segment_duration: 최소 세그먼트 길이 (초), 이보다 짧은 세그먼트는 제외
    
    Returns:
        [{"start": float, "end": float, "speaker": str, "embedding": list[float]}, ...] 형태의 리스트 또는 None
    """
    try:
        print(f"[Diarization] Starting segment embeddings extraction...")
        print(f"[Diarization] Pipeline type: {type(pipeline)}")
        print(f"[Diarization] Pipeline has _embedding: {hasattr(pipeline, '_embedding')}")
        
        # pipeline 내부의 embedding 모델에 접근
        if not hasattr(pipeline, '_embedding'):
            print("[Diarization] Pipeline does not have _embedding attribute")
            return None
        
        embedding_model = pipeline._embedding
        print(f"[Diarization] Embedding model type: {type(embedding_model)}")
        print(f"[Diarization] Embedding model has 'model' attribute: {hasattr(embedding_model, 'model')}")
        print(f"[Diarization] Embedding model has 'model_' attribute: {hasattr(embedding_model, 'model_')}")
        print(f"[Diarization] Embedding model is callable: {callable(embedding_model)}")
        
        # PyannoteAudioPretrainedSpeakerEmbedding은 model_ 속성을 사용하거나 직접 호출 가능
        if hasattr(embedding_model, 'model_'):
            actual_model = embedding_model.model_
            use_callable = False
        elif hasattr(embedding_model, 'model'):
            actual_model = embedding_model.model
            use_callable = False
        elif callable(embedding_model):
            actual_model = embedding_model
            use_callable = True
        else:
            print("[Diarization] Embedding model not found (no model, model_, or callable)")
            return None
        
        segment_embeddings = []
        waveform = audio_data["waveform"]
        sample_rate = audio_data["sample_rate"]
        
        print(f"[Diarization] Waveform shape: {waveform.shape}, sample_rate: {sample_rate}")
        print(f"[Diarization] Extracting embeddings for each segment (min_duration={min_segment_duration}s)...")
        
        total_segments = 0
        skipped_short = 0
        skipped_invalid = 0
        
        # 각 세그먼트마다 임베딩 추출
        for turn, _, speaker in diarization_result.itertracks(yield_label=True):
            total_segments += 1
            start_time = turn.start
            end_time = turn.end
            duration = end_time - start_time
            
            # 너무 짧은 세그먼트는 제외
            if duration < min_segment_duration:
                skipped_short += 1
                continue
            
            # 해당 구간의 오디오 추출
            start_sample = int(start_time * sample_rate)
            end_sample = int(end_time * sample_rate)
            
            # waveform shape 확인 및 샘플 범위 검증
            if len(waveform.shape) == 2:
                # (batch, samples) 형태
                max_samples = waveform.shape[1]
                if start_sample >= max_samples or end_sample <= start_sample:
                    skipped_invalid += 1
                    continue
                end_sample = min(end_sample, max_samples)
                segment_waveform = waveform[0, start_sample:end_sample]  # (samples,)
            elif len(waveform.shape) == 3:
                # (batch, channels, samples) 형태
                max_samples = waveform.shape[2]
                if start_sample >= max_samples or end_sample <= start_sample:
                    skipped_invalid += 1
                    continue
                end_sample = min(end_sample, max_samples)
                segment_waveform = waveform[0, :, start_sample:end_sample]  # (channels, samples)
            else:
                skipped_invalid += 1
                continue
            
            # Embedding 추출
            try:
                with torch.inference_mode():
                    # PyannoteAudioPretrainedSpeakerEmbedding 입력 형식: (batch_size, num_channels, num_samples)
                    # segment_waveform을 올바른 형태로 변환
                    if len(segment_waveform.shape) == 1:
                        # (samples,) -> (1, 1, samples)
                        segment_waveform_input = segment_waveform.unsqueeze(0).unsqueeze(0)
                    elif len(segment_waveform.shape) == 2:
                        # (channels, samples) -> (1, channels, samples)
                        segment_waveform_input = segment_waveform.unsqueeze(0)
                    else:
                        # 이미 (batch, channels, samples) 형태
                        segment_waveform_input = segment_waveform
                    
                    if use_callable:
                        # PyannoteAudioPretrainedSpeakerEmbedding은 직접 호출 가능
                        embedding = actual_model(segment_waveform_input)
                    else:
                        # model_ 속성을 사용하는 경우
                        embedding = actual_model(segment_waveform_input)
                    
                    # 결과 처리
                    if isinstance(embedding, torch.Tensor):
                        embedding = embedding.cpu().numpy()
                    if isinstance(embedding, np.ndarray):
                        # PyannoteAudioPretrainedSpeakerEmbedding은 (batch_size, dimension) 형태로 반환
                        # batch_size=1이므로 첫 번째 요소만 사용
                        if len(embedding.shape) == 2 and embedding.shape[0] == 1:
                            embedding = embedding[0]
                        # 평균 풀링 (여러 프레임이 있는 경우)
                        elif len(embedding.shape) > 1:
                            embedding = np.mean(embedding, axis=0)
                        embedding = embedding.tolist()
                
                segment_embeddings.append({
                    "start": start_time,
                    "end": end_time,
                    "speaker": speaker,
                    "duration": duration,
                    "embedding": embedding,
                })
            except Exception as seg_e:
                print(f"[Diarization] Error extracting embedding for segment {start_time:.2f}s-{end_time:.2f}s: {seg_e}")
                skipped_invalid += 1
                continue
        
        print(f"[Diarization] Segment embedding extraction summary:")
        print(f"  - Total segments: {total_segments}")
        print(f"  - Extracted: {len(segment_embeddings)}")
        print(f"  - Skipped (too short): {skipped_short}")
        print(f"  - Skipped (invalid): {skipped_invalid}")
        
        return segment_embeddings if segment_embeddings else None
    
    except Exception as e:
        print(f"[Diarization] Error extracting segment embeddings: {e}")
        import traceback
        traceback.print_exc()
        return None


def extract_speaker_embeddings(
    pipeline: Any,
    audio_data: dict[str, Any],
    diarization_result: Any,
) -> dict[str, list[float]] | None:
    """
    화자별 embedding 벡터를 추출합니다.
    
    Args:
        pipeline: pyannote.audio Pipeline 객체
        audio_data: 오디오 데이터 딕셔너리
        diarization_result: 화자 분리 결과 (Annotation 객체)
    
    Returns:
        {speaker_label: embedding_vector} 형태의 딕셔너리 또는 None
    """
    try:
        # pipeline 내부의 embedding 모델에 접근
        if not hasattr(pipeline, '_embedding'):
            print("[Diarization] Pipeline does not have _embedding attribute")
            return None
        
        embedding_model = pipeline._embedding
        
        # PyannoteAudioPretrainedSpeakerEmbedding은 model_ 속성을 사용하거나 직접 호출 가능
        if hasattr(embedding_model, 'model_'):
            actual_model = embedding_model.model_
            use_callable = False
        elif hasattr(embedding_model, 'model'):
            actual_model = embedding_model.model
            use_callable = False
        elif callable(embedding_model):
            actual_model = embedding_model
            use_callable = True
        else:
            print("[Diarization] Embedding model not found (no model, model_, or callable)")
            return None
        
        # 각 화자별로 대표 세그먼트를 선택하여 embedding 추출
        speaker_embeddings = {}
        waveform = audio_data["waveform"]
        sample_rate = audio_data["sample_rate"]
        
        for speaker in diarization_result.labels():
            # 해당 화자의 모든 세그먼트 찾기
            speaker_segments = [
                (turn.start, turn.end)
                for turn, _, spk in diarization_result.itertracks(yield_label=True)
                if spk == speaker
            ]
            
            if not speaker_segments:
                continue
            
            # 가장 긴 세그먼트를 선택 (또는 여러 세그먼트의 평균 사용 가능)
            longest_seg = max(speaker_segments, key=lambda x: x[1] - x[0])
            start_time, end_time = longest_seg
            
            # 해당 구간의 오디오 추출
            start_sample = int(start_time * sample_rate)
            end_sample = int(end_time * sample_rate)
            segment_waveform = waveform[:, start_sample:end_sample]
            
            # Embedding 추출
            with torch.inference_mode():
                if use_callable:
                    # PyannoteAudioPretrainedSpeakerEmbedding은 직접 호출 가능
                    # 입력 형식: (batch_size, num_channels, num_samples)
                    if len(segment_waveform.shape) == 2:
                        # (channels, samples) -> (1, channels, samples)
                        segment_waveform = segment_waveform.unsqueeze(0)
                    elif len(segment_waveform.shape) == 1:
                        # (samples,) -> (1, 1, samples)
                        segment_waveform = segment_waveform.unsqueeze(0).unsqueeze(0)
                    
                    embedding = actual_model(segment_waveform)
                else:
                    # model_ 속성을 사용하는 경우
                    embedding = actual_model(segment_waveform)
                
                # 결과 처리
                if isinstance(embedding, torch.Tensor):
                    embedding = embedding.cpu().numpy()
                if isinstance(embedding, np.ndarray):
                    # PyannoteAudioPretrainedSpeakerEmbedding은 (batch_size, dimension) 형태로 반환
                    # batch_size=1이므로 첫 번째 요소만 사용
                    if len(embedding.shape) == 2 and embedding.shape[0] == 1:
                        embedding = embedding[0]
                    # 평균 풀링 (여러 프레임이 있는 경우)
                    elif len(embedding.shape) > 1:
                        embedding = np.mean(embedding, axis=0)
                    embedding = embedding.tolist()
            
            speaker_embeddings[speaker] = embedding
        
        return speaker_embeddings if speaker_embeddings else None
    
    except Exception as e:
        print(f"[Diarization] Error extracting embeddings: {e}")
        return None


def extract_speaker_segments(
    diarization_result: Any,
    include_metadata: bool = False,
) -> list[tuple[float, float, str] | dict[str, Any]]:
    """
    화자 분리 결과에서 세그먼트 추출.
    
    Args:
        diarization_result: pyannote.audio의 Annotation 객체
        include_metadata: True일 경우 딕셔너리 형태로 메타데이터 포함
    
    Returns:
        include_metadata=False: [(start, end, speaker), ...]
        include_metadata=True: [{"start": float, "end": float, "speaker": str, "duration": float}, ...]
    """
    segments = []
    for turn, _, speaker in diarization_result.itertracks(yield_label=True):
        if include_metadata:
            segments.append({
                "start": turn.start,
                "end": turn.end,
                "speaker": speaker,
                "duration": turn.end - turn.start,
            })
        else:
            segments.append((turn.start, turn.end, speaker))
    
    if include_metadata:
        segments.sort(key=lambda x: x["start"])
    else:
        segments.sort(key=lambda x: x[0])
    
    return segments


def compute_speaker_transitions(segments: list[tuple[float, float, str]]) -> list[dict[str, Any]]:
    """화자 전환 지점 계산."""
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
        transitions.append({
            "point": center,
            "gap": gap,
            "start": transition_start,
            "end": transition_end,
        })
    return transitions


def find_optimal_split_points(
    diarization_result: Any,
    audio_duration: float,
    num_chunks: int,
) -> list[float]:
    """화자 분리 결과를 기반으로 여러 분할 지점을 계산한다."""
    if num_chunks <= 1:
        return []
    
    segments = extract_speaker_segments(diarization_result)
    transitions = compute_speaker_transitions(segments)
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


def build_nominal_ranges(audio_duration: float, boundary_points: list[float]) -> list[tuple[float, float]]:
    """분할 지점 리스트를 기반으로 (start, end) 구간 리스트 생성."""
    ranges = []
    prev = 0.0
    for boundary in boundary_points:
        boundary = max(prev + 1e-3, min(audio_duration - 1e-3, boundary))
        ranges.append((prev, boundary))
        prev = boundary
    ranges.append((prev, audio_duration))
    return ranges


def compute_segment_confidence(
    segment_start: float,
    segment_end: float,
    speaker: str,
    all_segments: list[dict[str, Any]],
    embeddings_dict: dict[str, list[float]] | None = None,
) -> dict[str, Any]:
    """
    세그먼트의 신뢰도 지표를 계산합니다.
    
    Args:
        segment_start: 세그먼트 시작 시간
        segment_end: 세그먼트 종료 시간
        speaker: 화자 라벨
        all_segments: 모든 화자 세그먼트 리스트
        embeddings_dict: 화자별 embedding 딕셔너리 (선택적)
    
    Returns:
        신뢰도 메타데이터 딕셔너리
    """
    duration = segment_end - segment_start
    
    # 1. 세그먼트 길이 기반 신뢰도 (너무 짧은 세그먼트는 신뢰도 낮음)
    length_confidence = min(1.0, duration / 2.0)  # 2초 이상이면 최대 신뢰도
    
    # 2. 인접 세그먼트와의 일관성 (같은 화자가 연속적으로 나오는지)
    continuity_score = 0.0
    for seg in all_segments:
        if seg.get("speaker") == speaker:
            # 시간적으로 가까운 세그먼트인지 확인
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            gap_before = max(0, segment_start - seg_end)
            gap_after = max(0, seg_start - segment_end)
            if gap_before < 1.0 or gap_after < 1.0:  # 1초 이내
                continuity_score += 1.0
    
    continuity_confidence = min(1.0, continuity_score / 3.0)  # 정규화
    
    # 3. Embedding 기반 신뢰도 (같은 화자의 다른 세그먼트와 유사도)
    embedding_confidence = None
    if embeddings_dict and speaker in embeddings_dict:
        # 같은 화자의 다른 세그먼트들과 embedding 유사도 계산 가능
        # 여기서는 기본값만 반환 (실제 계산은 필요시 구현)
        embedding_confidence = 0.8  # placeholder
    
    # 종합 신뢰도 (가중 평균)
    overall_confidence = (
        length_confidence * 0.4 +
        continuity_confidence * 0.4 +
        (embedding_confidence or 0.5) * 0.2
    )
    
    return {
        "length_confidence": length_confidence,
        "continuity_confidence": continuity_confidence,
        "embedding_confidence": embedding_confidence,
        "overall_confidence": overall_confidence,
        "duration": duration,
    }


def refine_diarization_with_confidence(
    diarization_result: Any,
    embeddings_dict: dict[str, list[float]] | None = None,
    min_confidence: float = 0.3,
) -> list[dict[str, Any]]:
    """
    화자 분리 결과를 신뢰도 기반으로 정제합니다.
    
    Args:
        diarization_result: pyannote.audio의 Annotation 객체
        embeddings_dict: 화자별 embedding 딕셔너리 (선택적)
        min_confidence: 최소 신뢰도 임계값 (이보다 낮으면 필터링)
    
    Returns:
        정제된 세그먼트 리스트 (신뢰도 메타데이터 포함)
    """
    # 모든 세그먼트 추출
    all_segments = extract_speaker_segments(diarization_result, include_metadata=True)
    
    # 각 세그먼트에 신뢰도 계산
    refined_segments = []
    for seg in all_segments:
        confidence_meta = compute_segment_confidence(
            seg["start"],
            seg["end"],
            seg["speaker"],
            all_segments,
            embeddings_dict,
        )
        
        seg_with_confidence = {
            **seg,
            **confidence_meta,
        }
        
        # 신뢰도가 임계값 이상인 경우만 포함
        if seg_with_confidence["overall_confidence"] >= min_confidence:
            refined_segments.append(seg_with_confidence)
        else:
            # 신뢰도가 낮은 세그먼트는 인접 세그먼트와 병합 고려
            # 여기서는 단순히 제외하지만, 필요시 병합 로직 추가 가능
            print(
                f"[Diarization] Low confidence segment filtered: "
                f"{seg['start']:.2f}s-{seg['end']:.2f}s "
                f"(confidence={seg_with_confidence['overall_confidence']:.2f})"
            )
    
    return refined_segments


def merge_segments_with_speakers(
    asr_segments: list[dict[str, Any]],
    diarization_result: Any,
    embeddings_dict: dict[str, list[float]] | None = None,
) -> list[dict[str, Any]]:
    """
    ASR 세그먼트에 화자 정보 추가 (신뢰도 메타데이터 포함).
    
    Args:
        asr_segments: ASR 결과 세그먼트 리스트
        diarization_result: pyannote.audio의 Annotation 객체
        embeddings_dict: 화자별 embedding 딕셔너리 (선택적)
    """
    # 화자 세그먼트를 딕셔너리로 변환 (빠른 조회)
    speaker_segments = {}
    for turn, _, speaker in diarization_result.itertracks(yield_label=True):
        speaker_segments[(turn.start, turn.end)] = speaker
    
    # 모든 화자 세그먼트 리스트 (신뢰도 계산용)
    all_diarization_segments = extract_speaker_segments(diarization_result, include_metadata=True)
    
    # 각 ASR 세그먼트에 가장 가까운 화자 할당
    merged_segments = []
    for seg in asr_segments:
        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_mid = (seg_start + seg_end) / 2
        
        # 가장 겹치는 화자 찾기
        best_speaker = None
        max_overlap = 0
        best_overlap_ratio = 0.0
        
        for (spk_start, spk_end), speaker in speaker_segments.items():
            # 겹치는 구간 계산
            overlap_start = max(seg_start, spk_start)
            overlap_end = min(seg_end, spk_end)
            overlap = max(0, overlap_end - overlap_start)
            
            # 겹침 비율 계산
            seg_duration = seg_end - seg_start
            overlap_ratio = overlap / seg_duration if seg_duration > 0 else 0
            
            if overlap > max_overlap:
                max_overlap = overlap
                best_speaker = speaker
                best_overlap_ratio = overlap_ratio
        
        # 세그먼트 중간점이 포함된 화자 찾기 (겹침이 없을 경우)
        if best_speaker is None:
            for (spk_start, spk_end), speaker in speaker_segments.items():
                if spk_start <= seg_mid <= spk_end:
                    best_speaker = speaker
                    best_overlap_ratio = 0.5  # 중간점 매칭은 중간 신뢰도
                    break
        
        seg["speaker"] = best_speaker or "UNKNOWN"
        
        # 신뢰도 메타데이터 추가
        if best_speaker:
            confidence_meta = compute_segment_confidence(
                seg_start,
                seg_end,
                best_speaker,
                all_diarization_segments,
                embeddings_dict,
            )
            # 겹침 비율도 신뢰도에 반영
            confidence_meta["overlap_ratio"] = best_overlap_ratio
            confidence_meta["overall_confidence"] = (
                confidence_meta["overall_confidence"] * 0.7 + best_overlap_ratio * 0.3
            )
            seg.update(confidence_meta)
        else:
            seg["overall_confidence"] = 0.0
            seg["overlap_ratio"] = 0.0
        
        merged_segments.append(seg)
    
    return merged_segments


def refine_speaker_assignment_with_embeddings(
    diarization_result: Any,
    segment_embeddings: list[dict[str, Any]],
    speaker_embeddings: dict[str, list[float]],
    similarity_threshold: float = 0.7,
) -> Any:
    """
    세그먼트 임베딩과 화자 임베딩 간의 코사인 유사도를 계산하여
    잘못 할당된 세그먼트를 재할당합니다.
    
    Args:
        diarization_result: pyannote.audio의 Annotation 객체
        segment_embeddings: 시간대별 세그먼트 임베딩 리스트
        speaker_embeddings: 화자별 임베딩 딕셔너리
        similarity_threshold: 최소 유사도 임계값 (0.0 ~ 1.0)
    
    Returns:
        재할당된 Annotation 객체
    """
    try:
        from scipy.spatial.distance import cosine
        from pyannote.core import Annotation
        
        refined_result = Annotation()
        
        # 세그먼트 임베딩을 시간순으로 정렬
        sorted_segments = sorted(segment_embeddings, key=lambda x: x['start'])
        
        reassigned_count = 0
        for seg_emb in sorted_segments:
            segment_emb_vector = np.array(seg_emb['embedding'])
            original_speaker = seg_emb['speaker']
            best_speaker = original_speaker
            best_similarity = -1
            
            # 각 화자 임베딩과 비교
            for speaker, speaker_emb_vector in speaker_embeddings.items():
                try:
                    similarity = 1 - cosine(segment_emb_vector, np.array(speaker_emb_vector))
                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_speaker = speaker
                except Exception as e:
                    print(f"[Diarization] Error computing similarity: {e}")
                    continue
            
            # 유사도가 임계값 이상이고 원래 화자와 다른 경우 재할당
            if best_similarity >= similarity_threshold and best_speaker != original_speaker:
                reassigned_count += 1
                print(
                    f"[Diarization] Reassigned segment {seg_emb['start']:.2f}s-{seg_emb['end']:.2f}s: "
                    f"{original_speaker} -> {best_speaker} (similarity={best_similarity:.3f})"
                )
            
            refined_result[seg_emb['start']:seg_emb['end'], best_speaker] = True
        
        if reassigned_count > 0:
            print(f"[Diarization] Reassigned {reassigned_count} segments based on embedding similarity")
        
        return refined_result
    
    except ImportError:
        print("[Diarization] scipy not available, skipping embedding-based refinement")
        return diarization_result
    except Exception as e:
        print(f"[Diarization] Error in embedding-based refinement: {e}")
        import traceback
        traceback.print_exc()
        return diarization_result


def merge_adjacent_segments(
    diarization_result: Any,
    max_gap_duration: float = 0.5,
    min_segment_duration: float = 0.3,
) -> Any:
    """
    인접한 동일 화자 세그먼트를 병합합니다.
    
    Args:
        diarization_result: pyannote.audio의 Annotation 객체
        max_gap_duration: 병합할 최대 간격 (초)
        min_segment_duration: 최소 세그먼트 길이 (초), 이보다 짧은 세그먼트는 병합
    
    Returns:
        병합된 Annotation 객체
    """
    try:
        from pyannote.core import Annotation
        
        merged_result = Annotation()
        
        # 화자별로 세그먼트 그룹화
        speaker_segments = {}
        for turn, _, speaker in diarization_result.itertracks(yield_label=True):
            if speaker not in speaker_segments:
                speaker_segments[speaker] = []
            speaker_segments[speaker].append((turn.start, turn.end))
        
        merged_count = 0
        
        # 각 화자의 세그먼트를 시간순으로 정렬하고 병합
        for speaker, segments in speaker_segments.items():
            segments.sort(key=lambda x: x[0])
            merged_segments = []
            current_start, current_end = segments[0]
            
            for start, end in segments[1:]:
                gap = start - current_end
                if gap <= max_gap_duration:
                    # 간격이 작으면 병합
                    current_end = end
                    merged_count += 1
                else:
                    # 간격이 크면 새 세그먼트 시작
                    if current_end - current_start >= min_segment_duration:
                        merged_segments.append((current_start, current_end))
                    current_start, current_end = start, end
            
            # 마지막 세그먼트 추가
            if current_end - current_start >= min_segment_duration:
                merged_segments.append((current_start, current_end))
            
            # 병합된 세그먼트를 결과에 추가
            for start, end in merged_segments:
                merged_result[start:end, speaker] = True
        
        if merged_count > 0:
            print(f"[Diarization] Merged {merged_count} adjacent segments")
        
        return merged_result
    
    except Exception as e:
        print(f"[Diarization] Error in segment merging: {e}")
        import traceback
        traceback.print_exc()
        return diarization_result

