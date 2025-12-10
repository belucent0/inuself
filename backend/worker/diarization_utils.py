"""화자분리 유틸리티."""
import sys
import time
from pathlib import Path
from typing import Any

from . import rocm_env as _rocm_env  # noqa: F401  # sys.path side-effect

import numpy as np
# PyTorch와 pyannote.audio는 lazy import로 처리 (torchaudio DLL 로드 오류 방지)

# logger import를 위한 경로 조정
_backend_dir = Path(__file__).parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

try:
    from app.core.logging import logger
except ImportError:
    # fallback: 직접 경로 구성
    _app_dir = _backend_dir / "app"
    if str(_app_dir) not in sys.path:
        sys.path.insert(0, str(_app_dir))
    from core.logging import logger


# HuggingFace Hub validation 패치 (로컬 경로 허용) - community-1 모델 로딩에 필수
try:
    import huggingface_hub.utils._validators
    import os
    
    if not hasattr(huggingface_hub.utils._validators, '_original_validate_repo_id'):
        huggingface_hub.utils._validators._original_validate_repo_id = huggingface_hub.utils._validators.validate_repo_id
    
    def _patched_validate_repo_id(repo_id: str) -> None:
        """로컬 경로인 경우 검증을 건너뛰는 패치된 validate_repo_id"""
        # 로컬 파일 시스템 경로로 보이면 검증 스킵 (Windows/Unix 경로 모두 처리)
        if os.path.exists(str(repo_id)) or '\\' in str(repo_id) or ':' in str(repo_id) or str(repo_id).startswith('/'):
            return
        # Hub ID인 경우에만 원래 검증 실행
        return huggingface_hub.utils._validators._original_validate_repo_id(repo_id)
    
    huggingface_hub.utils._validators.validate_repo_id = _patched_validate_repo_id
except (ImportError, AttributeError) as e:
    print(f"[Warning] Failed to patch validate_repo_id: {e}")

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

# pyannote.audio 3.1 호환성 패치 제거 (4.0.3 community-1은 최신 파이프라인 사용)
# SpeakerDiarization 클래스 직접 패치 불필요 -> 필요함! Community-1 config에 plda가 남아있어서 제거해야 함.
try:
    from pyannote.audio.pipelines import SpeakerDiarization
    import functools
    
    if not hasattr(SpeakerDiarization, '_original_init'):
        SpeakerDiarization._original_init = SpeakerDiarization.__init__
    
    @functools.wraps(SpeakerDiarization._original_init)
    def _patched_sd_init(self, *args, **kwargs):
        # plda 파라미터는 최신 버전에서 제거되었으므로 무시
        if "plda" in kwargs:
            print(f"[Patch] Ignoring 'plda' argument for SpeakerDiarization: {kwargs.get('plda')}")
            kwargs.pop("plda")
            
        SpeakerDiarization._original_init(self, *args, **kwargs)
    
    SpeakerDiarization.__init__ = _patched_sd_init
    print("[Patch] Applied SpeakerDiarization.__init__ patch for plda compatibility")
except (ImportError, AttributeError) as e:
    print(f"[Warning] Failed to patch SpeakerDiarization: {e}")

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
        device: 디바이스 ("cuda" 또는 "cpu", ROCm 환경에서는 "cuda" 사용)
        audio_duration: 오디오 길이 (초), 로그용
        return_embeddings: embedding 벡터도 반환할지 여부
        return_pipeline: pipeline 객체도 반환할지 여부 (시간대별 임베딩 추출용)
    
    Returns:
        (diarization_result, load_time, process_time, embeddings_dict, pipeline)
        - embeddings_dict: return_embeddings=True일 때만 제공
          {speaker_label: embedding_vector} 형태
        - pipeline: return_pipeline=True일 때만 제공
    """
    # Lazy import: torchaudio DLL 로드 오류 방지
    try:
        import torch
    except (OSError, ImportError, RuntimeError) as e:
        raise RuntimeError(
            f"Failed to import PyTorch: {e}\n"
            "This may be due to missing DLLs or incompatible PyTorch installation.\n"
            "Please ensure PyTorch with ROCm support is properly installed."
        ) from e
    
    try:
        from pyannote.audio import Pipeline as DiarizationPipeline
    except (OSError, ImportError, RuntimeError) as e:
        raise RuntimeError(
            f"Failed to import pyannote.audio: {e}\n"
            "This may be due to torchaudio DLL loading issues.\n"
            "Please ensure torchaudio with ROCm support is properly installed."
        ) from e
    
    if audio_duration:
        logger.info("[Diarization] Starting speaker diarization for entire audio file...")
        logger.info(f"[Diarization] Processing time range: 0.00s - {audio_duration:.2f}s ({audio_duration:.2f}s)")
    else:
        logger.info("[Diarization] Starting speaker diarization...")
    
    # 3.1 모델 지원 제거, 오직 community-1만 사용
    hf_model_id = "pyannote/speaker-diarization-community-1"
    
    logger.info(f"[Diarization] Loading speaker diarization model: {hf_model_id}")
    
    # community-1 모델 로딩 로직 (수동 패치 필수: Pipeline.from_pretrained가 $model 치환을 못함)
    # community-1 모델 로딩 로직 (수동 패치 필수: Pipeline.from_pretrained가 $model 치환을 못함)
    try:
        import yaml
        from huggingface_hub import snapshot_download
        from pyannote.audio import Model
        
        logger.info("[Diarization] Manually loading and patching community-1 model...")
        
        # 모델 다운로드
        model_path = snapshot_download(repo_id=hf_model_id)
        config_path = Path(model_path) / "config.yaml"
        
        # Config 로드
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        
        pipeline_params = config["pipeline"]["params"]
        logger.info(f"[Diarization] Initial params keys: {list(pipeline_params.keys())}")

        # plda 제거 및 segmentation/embedding 명시적 로딩
        for key in list(pipeline_params.keys()):
            value = pipeline_params[key]
            
            if key == "segmentation":
                logger.info("[Diarization] Explicitly loading segmentation model: pyannote/segmentation-3.0")
                # 커뮤니티 추천 버전에 맞춰 명시적 로드
                pipeline_params[key] = Model.from_pretrained("pyannote/segmentation-3.0")
                
            elif key == "embedding":
                logger.info("[Diarization] Explicitly loading embedding model: pyannote/wespeaker-voxceleb-resnet34-LM")
                # 커뮤니티 추천 버전에 맞춰 명시적 로드
                pipeline_params[key] = Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM")
                
            elif key == "plda":
                logger.info(f"[Diarization] Removing deprecated 'plda' key")
                del pipeline_params[key]
                
            elif isinstance(value, str) and "$model" in value:
                # 절대 경로로 변환 (Windows 역슬래시 문제 주의)
                replaced_path = value.replace("$model", str(model_path))
                pipeline_params[key] = replaced_path
        
        # Pipeline 인스턴스화
        diarization_load_start = time.time()
        
        from pyannote.audio.pipelines import SpeakerDiarization
        logger.info(f"[Diarization] Instantiating SpeakerDiarization with keys: {list(pipeline_params.keys())}")
        
        # 로컬 경로 검증 패치가 적용된 상태에서 실행됨
        diarization_pipeline = SpeakerDiarization(**pipeline_params)
        
        # 가중치 로드 (pytorch_model.bin)
        weights_path = Path(model_path) / "pytorch_model.bin"
        if weights_path.exists():
            logger.info(f"[Diarization] Loading weights from {weights_path}")
            diarization_pipeline.load_state_dict(torch.load(weights_path, map_location=device))
        else:
            logger.warning(f"[Diarization] Weights file not found at {weights_path}")
            
    except Exception as e:
        import traceback
        logger.error(f"[Diarization] Failed to manual load community-1: {e}")
        logger.error(traceback.format_exc())
        # Fallback은 하지 않음 (community-1 전용이므로)
        raise RuntimeError(f"Failed to load community-1 model: {e}")
        
    diarization_pipeline.to(torch.device(device))
    diarization_load_time = time.time() - diarization_load_start
    
    logger.info(f"[Diarization] Model loaded in {diarization_load_time:.2f} seconds")
    logger.info("[Diarization] Starting speaker diarization...")
    
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
            logger.info(f"[Diarization] Using fixed number of speakers: {num_speakers}")
        elif min_speakers is not None or max_speakers is not None:
            if min_speakers is not None:
                pipeline_kwargs['min_speakers'] = min_speakers
            if max_speakers is not None:
                pipeline_kwargs['max_speakers'] = max_speakers
            logger.info(f"[Diarization] Using speaker range: min={min_speakers}, max={max_speakers}")
        
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
                        logger.warning(f"[Diarization] Warning: Unexpected embeddings shape: {embeddings.shape}")
                        embeddings_dict = None
                else:
                    embeddings_dict = None
            except TypeError:
                # return_embeddings 파라미터를 지원하지 않는 경우
                logger.info("[Diarization] return_embeddings not supported, extracting manually...")
                result = diarization_pipeline(audio_data, **pipeline_kwargs)
                
                # Pyannote 4.x 대응: DiarizeOutput 객체에서 Annotation 추출
                if hasattr(result, "speaker_diarization"):
                    logger.info("[Diarization] Extracting annotation from 4.x DiarizeOutput (fallback path)")
                    result = result.speaker_diarization

                embeddings_dict = extract_speaker_embeddings(
                    diarization_pipeline, audio_data, result
                )
        else:
            result = diarization_pipeline(audio_data, **pipeline_kwargs)
            
            # Pyannote 4.x 대응: DiarizeOutput 객체에서 Annotation 추출
            if hasattr(result, "speaker_diarization"):
                logger.info("[Diarization] Extracting annotation from 4.x DiarizeOutput")
                result = result.speaker_diarization

            embeddings_dict = None
    diarization_time = time.time() - diarization_start
    
    logger.info(f"[Diarization] Completed in {diarization_time:.2f} seconds")
    
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
        logger.info("[Diarization] Starting segment embeddings extraction...")
        logger.debug(f"[Diarization] Pipeline type: {type(pipeline)}")
        logger.debug(f"[Diarization] Pipeline has _embedding: {hasattr(pipeline, '_embedding')}")
        
        # pipeline 내부의 embedding 모델에 접근
        if not hasattr(pipeline, '_embedding'):
            logger.warning("[Diarization] Pipeline does not have _embedding attribute")
            return None
        
        embedding_model = pipeline._embedding
        logger.debug(f"[Diarization] Embedding model type: {type(embedding_model)}")
        logger.debug(f"[Diarization] Embedding model has 'model' attribute: {hasattr(embedding_model, 'model')}")
        logger.debug(f"[Diarization] Embedding model has 'model_' attribute: {hasattr(embedding_model, 'model_')}")
        logger.debug(f"[Diarization] Embedding model is callable: {callable(embedding_model)}")
        
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
            logger.warning("[Diarization] Embedding model not found (no model, model_, or callable)")
            return None
        
        segment_embeddings = []
        waveform = audio_data["waveform"]
        sample_rate = audio_data["sample_rate"]
        
        logger.debug(f"[Diarization] Waveform shape: {waveform.shape}, sample_rate: {sample_rate}")
        
        # 전체 세그먼트 수 미리 계산 (진행 상황 표시용)
        all_segments = list(diarization_result.itertracks(yield_label=True))
        total_segments_count = len(all_segments)
        logger.info(f"[Diarization] Total segments to process: {total_segments_count}")
        logger.info(f"[Diarization] Extracting embeddings for each segment (min_duration={min_segment_duration}s)...")
        
        total_segments = 0
        skipped_short = 0
        skipped_invalid = 0
        
        # 각 세그먼트마다 임베딩 추출
        for idx, (turn, _, speaker) in enumerate(all_segments, 1):
            total_segments += 1
            
            # 진행 상황 출력 (10개마다 또는 마지막)
            if idx % 10 == 0 or idx == total_segments_count:
                logger.info(f"[Diarization] Processing segment {idx}/{total_segments_count}...")
            
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
                    
                    # GPU 메모리 정리 (매 50개 세그먼트마다, ROCm 환경에서도 작동)
                    if idx % 50 == 0 and torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    
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
                logger.error(f"[Diarization] Error extracting embedding for segment {start_time:.2f}s-{end_time:.2f}s (segment {idx}/{total_segments_count}): {seg_e}")
                import traceback
                traceback.print_exc()
                skipped_invalid += 1
                continue
        
        logger.info("[Diarization] Segment embedding extraction summary:")
        logger.info(f"  - Total segments: {total_segments}")
        logger.info(f"  - Extracted: {len(segment_embeddings)}")
        logger.info(f"  - Skipped (too short): {skipped_short}")
        logger.info(f"  - Skipped (invalid): {skipped_invalid}")
        
        return segment_embeddings if segment_embeddings else None
    
    except Exception as e:
        logger.error(f"[Diarization] Error extracting segment embeddings: {e}")
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
            logger.warning("[Diarization] Pipeline does not have _embedding attribute")
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
            logger.warning("[Diarization] Embedding model not found (no model, model_, or callable)")
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
        logger.error(f"[Diarization] Error extracting embeddings: {e}")
        return None


def extract_speaker_segments(
    diarization_result: Any,
    include_metadata: bool = False,
    split_overlaps: bool = False,
) -> list[tuple[float, float, str] | dict[str, Any]]:
    """
    화자 분리 결과에서 세그먼트 추출.
    
    Args:
        diarization_result: pyannote.audio의 Annotation 객체
        include_metadata: True일 경우 딕셔너리 형태로 메타데이터 포함
        split_overlaps: True일 경우 겹치는 구간을 분리하여 별도 세그먼트로 생성
    
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
    
    # 겹치는 구간을 분리하는 경우
    if split_overlaps:
        segments = _split_overlapping_segments(segments, include_metadata)
    
    if include_metadata:
        segments.sort(key=lambda x: x["start"])
    else:
        segments.sort(key=lambda x: x[0])
    
    return segments


def _split_overlapping_segments(
    segments: list[tuple[float, float, str] | dict[str, Any]],
    include_metadata: bool,
) -> list[tuple[float, float, str] | dict[str, Any]]:
    """
    겹치는 세그먼트를 분리하여 별도 세그먼트로 생성합니다.
    
    예시:
        화자 A: 0.0 ~ 5.0초
        화자 B: 3.0 ~ 4.0초 (끼어듦)
        
        결과:
        - 화자 A: 0.0 ~ 3.0초, 4.0 ~ 5.0초
        - 화자 B: 3.0 ~ 4.0초
    
    Args:
        segments: 세그먼트 리스트
        include_metadata: 메타데이터 포함 여부
    
    Returns:
        분리된 세그먼트 리스트
    """
    if not segments:
        return segments
    
    # 모든 시간 경계점 수집 (시작점과 끝점)
    time_points = set()
    for seg in segments:
        if include_metadata:
            time_points.add(seg["start"])
            time_points.add(seg["end"])
        else:
            time_points.add(seg[0])
            time_points.add(seg[1])
    
    time_points = sorted(time_points)
    
    # 각 시간 구간에서 활성화된 화자 찾기
    split_segments = []
    for i in range(len(time_points) - 1):
        start_time = time_points[i]
        end_time = time_points[i + 1]
        
        # 이 구간에서 활성화된 모든 화자 찾기
        active_speakers = []
        for seg in segments:
            if include_metadata:
                seg_start = seg["start"]
                seg_end = seg["end"]
                seg_speaker = seg["speaker"]
            else:
                seg_start = seg[0]
                seg_end = seg[1]
                seg_speaker = seg[2]
            
            # 겹치는지 확인 (구간이 완전히 포함되거나 부분적으로 겹침)
            if seg_start < end_time and seg_end > start_time:
                active_speakers.append(seg_speaker)
        
        # 각 활성화된 화자에 대해 세그먼트 생성
        for speaker in active_speakers:
            if include_metadata:
                split_segments.append({
                    "start": start_time,
                    "end": end_time,
                    "speaker": speaker,
                    "duration": end_time - start_time,
                    "is_overlap": len(active_speakers) > 1,  # 겹침 여부 표시
                })
            else:
                split_segments.append((start_time, end_time, speaker))
    
    return split_segments


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
            logger.info(
                f"[Split] Using speaker transition near {target:.2f}s -> {selected:.2f}s "
                f"(gap {candidates[0]['gap']:.2f}s)"
            )
        else:
            selected = target
            logger.info(
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
            logger.debug(
                f"[Diarization] Low confidence segment filtered: "
                f"{seg['start']:.2f}s-{seg['end']:.2f}s "
                f"(confidence={seg_with_confidence['overall_confidence']:.2f})"
            )
    
    return refined_segments


def merge_segments_with_speakers(
    asr_segments: list[dict[str, Any]],
    diarization_result: Any,
    embeddings_dict: dict[str, list[float]] | None = None,
    split_overlaps: bool = True,
) -> list[dict[str, Any]]:
    """
    ASR 세그먼트에 화자 정보 추가 (신뢰도 메타데이터 포함).
    
    Args:
        asr_segments: ASR 결과 세그먼트 리스트
        diarization_result: pyannote.audio의 Annotation 객체
        embeddings_dict: 화자별 embedding 딕셔너리 (선택적)
        split_overlaps: True일 경우 겹치는 구간을 분리하여 처리
    """
    # 화자 세그먼트를 딕셔너리로 변환 (빠른 조회)
    # 겹침 분리 옵션이 활성화된 경우, 분리된 세그먼트 사용
    speaker_segments = {}
    if split_overlaps:
        # 겹치는 구간을 분리한 세그먼트 사용
        split_diarization_segments = extract_speaker_segments(
            diarization_result, 
            include_metadata=True, 
            split_overlaps=True
        )
        for seg in split_diarization_segments:
            speaker_segments[(seg["start"], seg["end"])] = seg["speaker"]
    else:
        # 기존 방식: 겹침을 고려하지 않음
        for turn, _, speaker in diarization_result.itertracks(yield_label=True):
            speaker_segments[(turn.start, turn.end)] = speaker
    
    # 모든 화자 세그먼트 리스트 (신뢰도 계산용)
    all_diarization_segments = extract_speaker_segments(
        diarization_result, 
        include_metadata=True,
        split_overlaps=split_overlaps
    )
    
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
                    logger.error(f"[Diarization] Error computing similarity: {e}")
                    continue
            
            # 유사도가 임계값 이상이고 원래 화자와 다른 경우 재할당
            if best_similarity >= similarity_threshold and best_speaker != original_speaker:
                reassigned_count += 1
                logger.info(
                    f"[Diarization] Reassigned segment {seg_emb['start']:.2f}s-{seg_emb['end']:.2f}s: "
                    f"{original_speaker} -> {best_speaker} (similarity={best_similarity:.3f})"
                )
            
            refined_result[seg_emb['start']:seg_emb['end'], best_speaker] = True
        
        if reassigned_count > 0:
            logger.info(f"[Diarization] Reassigned {reassigned_count} segments based on embedding similarity")
        
        return refined_result
    
    except ImportError:
        logger.warning("[Diarization] scipy not available, skipping embedding-based refinement")
        return diarization_result
    except Exception as e:
        logger.error(f"[Diarization] Error in embedding-based refinement: {e}")
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
            logger.info(f"[Diarization] Merged {merged_count} adjacent segments")
        
        return merged_result
    
    except Exception as e:
        logger.error(f"[Diarization] Error in segment merging: {e}")
        import traceback
        traceback.print_exc()
        return diarization_result


def recluster_speakers_from_embeddings(
    segment_embeddings: list[dict[str, Any]],
    target_num_speakers: int | None = None,
    similarity_threshold: float = 0.7,
) -> dict[int, str]:
    """
    세그먼트 임베딩을 기반으로 코사인 유사도를 사용하여 화자를 재클러스터링합니다.
    
    Args:
        segment_embeddings: 시간대별 세그먼트 임베딩 리스트
            각 항목은 {"start": float, "end": float, "speaker": str, "embedding": list[float]} 형태
        target_num_speakers: 목표 화자 수 (None이면 자동 결정)
        similarity_threshold: 코사인 유사도 임계값 (0.0 ~ 1.0)
    
    Returns:
        {segment_index: new_speaker_label} 형태의 딕셔너리
    """
    try:
        from scipy.spatial.distance import cosine
        from collections import defaultdict
        
        if not segment_embeddings:
            return {}
        
        num_segments = len(segment_embeddings)
        logger.info(f"[Reclustering] Starting reclustering for {num_segments} segments")
        logger.info(f"[Reclustering] Target speakers: {target_num_speakers}, Similarity threshold: {similarity_threshold}")
        
        # 임베딩 벡터 추출
        embeddings = []
        for seg in segment_embeddings:
            emb = np.array(seg['embedding'])
            embeddings.append(emb)
        
        embeddings = np.array(embeddings)
        logger.debug(f"[Reclustering] Embeddings shape: {embeddings.shape}")
        
        # 코사인 유사도 행렬 계산
        similarity_matrix = np.zeros((num_segments, num_segments))
        for i in range(num_segments):
            for j in range(i + 1, num_segments):
                similarity = 1 - cosine(embeddings[i], embeddings[j])
                similarity_matrix[i, j] = similarity
                similarity_matrix[j, i] = similarity
        
        # 초기 그룹 생성: 유사도가 임계값 이상인 세그먼트들을 같은 그룹으로 묶음
        groups = []
        assigned = set()
        
        for i in range(num_segments):
            if i in assigned:
                continue
            
            # 새 그룹 시작
            current_group = [i]
            assigned.add(i)
            
            # 유사한 세그먼트 찾기
            for j in range(i + 1, num_segments):
                if j in assigned:
                    continue
                if similarity_matrix[i, j] >= similarity_threshold:
                    current_group.append(j)
                    assigned.add(j)
            
            groups.append(current_group)
        
        logger.info(f"[Reclustering] Initial groups: {len(groups)}")
        
        # 목표 화자 수에 맞춰 그룹 조정
        if target_num_speakers is not None:
            current_num_groups = len(groups)
            
            if current_num_groups > target_num_speakers:
                # 그룹 수가 많으면 병합 필요
                logger.info(f"[Reclustering] Merging {current_num_groups} groups to {target_num_speakers}")
                
                # 그룹 간 평균 유사도 계산
                group_embeddings = []
                for group in groups:
                    group_emb = np.mean([embeddings[idx] for idx in group], axis=0)
                    group_embeddings.append(group_emb)
                
                # 그룹 간 유사도 행렬
                group_similarity_matrix = np.zeros((len(groups), len(groups)))
                for i in range(len(groups)):
                    for j in range(i + 1, len(groups)):
                        similarity = 1 - cosine(group_embeddings[i], group_embeddings[j])
                        group_similarity_matrix[i, j] = similarity
                        group_similarity_matrix[j, i] = similarity
                
                # 가장 유사한 그룹부터 병합
                while len(groups) > target_num_speakers:
                    # 가장 유사한 두 그룹 찾기
                    max_similarity = -1
                    merge_i, merge_j = -1, -1
                    
                    for i in range(len(groups)):
                        for j in range(i + 1, len(groups)):
                            if group_similarity_matrix[i, j] > max_similarity:
                                max_similarity = group_similarity_matrix[i, j]
                                merge_i, merge_j = i, j
                    
                    if merge_i == -1:
                        break
                    
                    # 그룹 병합
                    groups[merge_i].extend(groups[merge_j])
                    groups.pop(merge_j)
                    
                    # 그룹 임베딩 재계산
                    group_embeddings = []
                    for group in groups:
                        group_emb = np.mean([embeddings[idx] for idx in group], axis=0)
                        group_embeddings.append(group_emb)
                    
                    # 유사도 행렬 재계산
                    group_similarity_matrix = np.zeros((len(groups), len(groups)))
                    for i in range(len(groups)):
                        for j in range(i + 1, len(groups)):
                            similarity = 1 - cosine(group_embeddings[i], group_embeddings[j])
                            group_similarity_matrix[i, j] = similarity
                            group_similarity_matrix[j, i] = similarity
                
            elif current_num_groups < target_num_speakers:
                # 그룹 수가 적으면 분리 필요
                logger.info(f"[Reclustering] Splitting groups from {current_num_groups} to {target_num_speakers}")
                
                # 가장 큰 그룹부터 분리
                while len(groups) < target_num_speakers:
                    # 가장 큰 그룹 찾기
                    largest_group_idx = max(range(len(groups)), key=lambda i: len(groups[i]))
                    largest_group = groups[largest_group_idx]
                    
                    if len(largest_group) < 2:
                        break
                    
                    # 그룹 내 세그먼트 간 유사도 계산
                    group_embeddings_list = [embeddings[idx] for idx in largest_group]
                    
                    # 가장 유사도가 낮은 두 세그먼트 찾기
                    min_similarity = float('inf')
                    split_idx1, split_idx2 = -1, -1
                    
                    for i in range(len(largest_group)):
                        for j in range(i + 1, len(largest_group)):
                            seg_i = largest_group[i]
                            seg_j = largest_group[j]
                            similarity = similarity_matrix[seg_i, seg_j]
                            if similarity < min_similarity:
                                min_similarity = similarity
                                split_idx1, split_idx2 = i, j
                    
                    if split_idx1 == -1:
                        break
                    
                    # 두 세그먼트를 기준으로 그룹 분리
                    seg1_idx = largest_group[split_idx1]
                    seg2_idx = largest_group[split_idx2]
                    
                    group1 = [seg1_idx]
                    group2 = [seg2_idx]
                    
                    for idx in largest_group:
                        if idx == seg1_idx or idx == seg2_idx:
                            continue
                        sim1 = similarity_matrix[idx, seg1_idx]
                        sim2 = similarity_matrix[idx, seg2_idx]
                        if sim1 > sim2:
                            group1.append(idx)
                        else:
                            group2.append(idx)
                    
                    # 원래 그룹을 두 그룹으로 교체
                    groups.pop(largest_group_idx)
                    groups.append(group1)
                    groups.append(group2)
        
        # 새로운 화자 라벨 생성 및 매핑
        new_labels = [f"SPEAKER_{i:02d}" for i in range(len(groups))]
        segment_to_speaker = {}
        
        for group_idx, group in enumerate(groups):
            speaker_label = new_labels[group_idx]
            for seg_idx in group:
                segment_to_speaker[seg_idx] = speaker_label
        
        logger.info(f"[Reclustering] Final groups: {len(groups)}")
        logger.info(f"[Reclustering] New speaker labels: {new_labels}")
        
        return segment_to_speaker
    
    except ImportError:
        logger.error("[Reclustering] scipy not available, cannot perform reclustering")
        raise ImportError("scipy is required for reclustering. Install it with: pip install scipy")
    except Exception as e:
        logger.error(f"[Reclustering] Error in reclustering: {e}")
        import traceback
        traceback.print_exc()
        raise


def update_transcription_with_new_speakers(
    transcription: dict[str, Any],
    segment_to_speaker_mapping: dict[int, str],
    segment_embeddings: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    transcription을 새로운 화자 라벨로 업데이트합니다.
    
    Args:
        transcription: 기존 transcription 딕셔너리
        segment_to_speaker_mapping: {segment_index: new_speaker_label} 매핑
        segment_embeddings: 시간대별 세그먼트 임베딩 리스트
    
    Returns:
        업데이트된 transcription 딕셔너리
    """
    import copy
    
    updated_transcription = copy.deepcopy(transcription)
    
    # 새로운 화자 라벨 리스트 생성
    new_speaker_labels = sorted(set(segment_to_speaker_mapping.values()))
    num_speakers = len(new_speaker_labels)
    
    logger.info(f"[Update] Updating transcription with {num_speakers} speakers: {new_speaker_labels}")
    
    # 1. segments의 speaker 필드 업데이트
    if 'segments' in updated_transcription:
        for seg_idx, segment in enumerate(updated_transcription['segments']):
            if seg_idx in segment_to_speaker_mapping:
                segment['speaker'] = segment_to_speaker_mapping[seg_idx]
    
    # 2. diarization_metadata 업데이트
    if 'diarization_metadata' not in updated_transcription:
        updated_transcription['diarization_metadata'] = {}
    
    metadata = updated_transcription['diarization_metadata']
    
    # 3. segment_embeddings의 speaker 필드 업데이트
    if 'segment_embeddings' in metadata:
        updated_segment_embeddings = []
        for seg_idx, seg_emb in enumerate(metadata['segment_embeddings']):
            if seg_idx in segment_to_speaker_mapping:
                updated_seg_emb = copy.deepcopy(seg_emb)
                updated_seg_emb['speaker'] = segment_to_speaker_mapping[seg_idx]
                updated_segment_embeddings.append(updated_seg_emb)
            else:
                updated_segment_embeddings.append(seg_emb)
        metadata['segment_embeddings'] = updated_segment_embeddings
    
    # 4. speaker_labels 업데이트
    metadata['speaker_labels'] = new_speaker_labels
    
    # 5. num_speakers 업데이트
    metadata['num_speakers'] = num_speakers
    
    # 6. speaker_embeddings 재계산 (각 새 화자의 대표 임베딩)
    speaker_embeddings = {}
    for speaker_label in new_speaker_labels:
        # 해당 화자의 모든 세그먼트 임베딩 수집
        speaker_segment_indices = [
            idx for idx, label in segment_to_speaker_mapping.items()
            if label == speaker_label
        ]
        
        if speaker_segment_indices:
            # 가장 긴 세그먼트의 임베딩을 대표로 사용
            speaker_segments = [
                (idx, seg_emb) for idx, seg_emb in enumerate(segment_embeddings)
                if idx in speaker_segment_indices
            ]
            
            if speaker_segments:
                # 가장 긴 세그먼트 선택
                longest_seg = max(speaker_segments, key=lambda x: x[1]['duration'])
                speaker_embeddings[speaker_label] = longest_seg[1]['embedding']
    
    metadata['speaker_embeddings'] = speaker_embeddings
    
    logger.info(f"[Update] Updated {len(segment_to_speaker_mapping)} segments")
    logger.info(f"[Update] New speaker embeddings: {list(speaker_embeddings.keys())}")
    
    return updated_transcription

