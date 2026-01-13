"""Diarization Service - pyannote.audio 화자분리 서비스."""
import logging
import asyncio
import time
from pathlib import Path
from functools import lru_cache
from typing import Optional, Any

import numpy as np

from ..config import get_settings
from ..models.schemas import DiarizationResponse, DiarizationSegment

logger = logging.getLogger(__name__)


# HuggingFace Hub 호환성 패치
def _apply_huggingface_patches():
    """pyannote.audio 호환성을 위한 HuggingFace Hub 패치 적용."""
    import os

    # 1. validate_repo_id 패치 (로컬 경로 허용)
    try:
        import huggingface_hub.utils._validators

        if not hasattr(huggingface_hub.utils._validators, '_original_validate_repo_id'):
            huggingface_hub.utils._validators._original_validate_repo_id = (
                huggingface_hub.utils._validators.validate_repo_id
            )

        def _patched_validate_repo_id(repo_id: str) -> None:
            if os.path.exists(str(repo_id)) or '\\' in str(repo_id) or ':' in str(repo_id):
                return
            return huggingface_hub.utils._validators._original_validate_repo_id(repo_id)

        huggingface_hub.utils._validators.validate_repo_id = _patched_validate_repo_id
    except (ImportError, AttributeError) as e:
        logger.warning(f"[Diarization] Failed to patch validate_repo_id: {e}")

    # 2. use_auth_token -> token 변환 패치
    try:
        import huggingface_hub
        import functools

        if not hasattr(huggingface_hub, '_original_hf_hub_download'):
            huggingface_hub._original_hf_hub_download = huggingface_hub.hf_hub_download

        @functools.wraps(huggingface_hub._original_hf_hub_download)
        def _patched_hf_hub_download(*args, **kwargs):
            if "use_auth_token" in kwargs:
                kwargs["token"] = kwargs.pop("use_auth_token")
            return huggingface_hub._original_hf_hub_download(*args, **kwargs)

        huggingface_hub.hf_hub_download = _patched_hf_hub_download
    except (ImportError, AttributeError):
        pass

    # 3. SpeakerDiarization plda 파라미터 무시 패치
    try:
        from pyannote.audio.pipelines import SpeakerDiarization
        import functools

        if not hasattr(SpeakerDiarization, '_original_init'):
            SpeakerDiarization._original_init = SpeakerDiarization.__init__

        @functools.wraps(SpeakerDiarization._original_init)
        def _patched_sd_init(self, *args, **kwargs):
            if "plda" in kwargs:
                kwargs.pop("plda")
            SpeakerDiarization._original_init(self, *args, **kwargs)

        SpeakerDiarization.__init__ = _patched_sd_init
    except (ImportError, AttributeError) as e:
        logger.warning(f"[Diarization] Failed to patch SpeakerDiarization: {e}")


# 모듈 로드 시 패치 적용
_apply_huggingface_patches()


class DiarizationService:
    """pyannote.audio 기반 화자분리 서비스."""

    def __init__(self):
        self._pipeline = None
        self._settings = get_settings()
        self._device = None

    def _load_pipeline(self):
        """파이프라인 로드 (lazy loading)."""
        if self._pipeline is not None:
            return

        import torch
        import yaml
        from huggingface_hub import snapshot_download
        from pyannote.audio import Model
        from pyannote.audio.pipelines import SpeakerDiarization

        logger.info("[DiarizationService] Loading pipeline...")
        start_time = time.time()

        hf_model_id = "pyannote/speaker-diarization-community-1"

        # 모델 다운로드
        model_path = snapshot_download(repo_id=hf_model_id)
        config_path = Path(model_path) / "config.yaml"

        # Config 로드
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        pipeline_params = config["pipeline"]["params"]

        # segmentation/embedding 명시적 로딩 및 plda 제거
        for key in list(pipeline_params.keys()):
            value = pipeline_params[key]

            if key == "segmentation":
                logger.info("[DiarizationService] Loading segmentation model: pyannote/segmentation-3.0")
                pipeline_params[key] = Model.from_pretrained("pyannote/segmentation-3.0")
            elif key == "embedding":
                logger.info("[DiarizationService] Loading embedding model: pyannote/wespeaker-voxceleb-resnet34-LM")
                pipeline_params[key] = Model.from_pretrained("pyannote/wespeaker-voxceleb-resnet34-LM")
            elif key == "plda":
                del pipeline_params[key]
            elif isinstance(value, str) and "$model" in value:
                pipeline_params[key] = value.replace("$model", str(model_path))

        # Pipeline 인스턴스화
        self._pipeline = SpeakerDiarization(**pipeline_params)

        # 하이퍼파라미터 설정
        HYPER_PARAMETERS = {
            "segmentation": {
                "min_duration_off": 0.0,
            },
            "clustering": {
                "threshold": self._settings.clustering_threshold,
                "Fa": 0.07,
                "Fb": 0.8
            }
        }
        self._pipeline.instantiate(HYPER_PARAMETERS)

        # 가중치 로드
        weights_path = Path(model_path) / "pytorch_model.bin"
        if weights_path.exists():
            self._device = f"cuda:{self._settings.whisper_device}" if torch.cuda.is_available() else "cpu"
            self._pipeline.load_state_dict(torch.load(weights_path, map_location=self._device))

        # GPU로 이동
        self._device = f"cuda:{self._settings.whisper_device}" if torch.cuda.is_available() else "cpu"
        self._pipeline.to(torch.device(self._device))

        load_time = time.time() - start_time
        logger.info(f"[DiarizationService] Pipeline loaded on {self._device} ({load_time:.2f}s)")

    async def diarize(
        self,
        audio_path: Path,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
        return_embeddings: bool = False,
    ) -> DiarizationResponse:
        """
        화자분리 실행.

        Args:
            audio_path: 오디오 파일 경로
            min_speakers: 최소 화자 수
            max_speakers: 최대 화자 수
            return_embeddings: 임베딩 반환 여부

        Returns:
            DiarizationResponse
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            self._diarize_sync,
            audio_path,
            min_speakers,
            max_speakers,
            return_embeddings,
        )
        return result

    def _diarize_sync(
        self,
        audio_path: Path,
        min_speakers: Optional[int],
        max_speakers: Optional[int],
        return_embeddings: bool,
    ) -> DiarizationResponse:
        """동기 화자분리 실행."""
        import torch
        import librosa

        self._load_pipeline()

        logger.info(f"[DiarizationService] Diarizing: {audio_path}")
        start_time = time.time()

        # 오디오 로드
        waveform, sample_rate = librosa.load(str(audio_path), sr=16000)
        duration = len(waveform) / sample_rate

        logger.info(f"[DiarizationService] Audio duration: {duration:.2f}s")

        # 입력 데이터 준비
        audio_data = {
            "waveform": torch.from_numpy(waveform).unsqueeze(0).to(self._device),
            "sample_rate": sample_rate
        }

        # 화자분리 실행
        pipeline_kwargs = {}
        if min_speakers:
            pipeline_kwargs["min_speakers"] = min_speakers
        if max_speakers:
            pipeline_kwargs["max_speakers"] = max_speakers

        with torch.inference_mode():
            result = self._pipeline(audio_data, **pipeline_kwargs)

            # Pyannote 4.x DiarizeOutput 처리
            if hasattr(result, "speaker_diarization"):
                result = result.speaker_diarization

        process_time = time.time() - start_time
        logger.info(f"[DiarizationService] Diarization completed in {process_time:.2f}s")

        # 세그먼트 추출
        segments = []
        speaker_labels = set()

        for turn, _, speaker in result.itertracks(yield_label=True):
            segments.append(DiarizationSegment(
                start=turn.start,
                end=turn.end,
                speaker=speaker,
                duration=turn.end - turn.start,
            ))
            speaker_labels.add(speaker)

        # 임베딩 추출 (선택적)
        embeddings = None
        if return_embeddings:
            embeddings = self._extract_speaker_embeddings(audio_data, result)

        return DiarizationResponse(
            segments=segments,
            num_speakers=len(speaker_labels),
            speaker_labels=sorted(list(speaker_labels)),
            duration=duration,
            embeddings=embeddings,
            process_time=process_time,
        )

    def _extract_speaker_embeddings(
        self,
        audio_data: dict[str, Any],
        diarization_result: Any,
    ) -> Optional[dict[str, list[float]]]:
        """화자별 임베딩 추출."""
        import torch

        try:
            if not hasattr(self._pipeline, '_embedding'):
                return None

            embedding_model = self._pipeline._embedding

            # 모델 접근
            if hasattr(embedding_model, 'model_'):
                actual_model = embedding_model.model_
            elif hasattr(embedding_model, 'model'):
                actual_model = embedding_model.model
            elif callable(embedding_model):
                actual_model = embedding_model
            else:
                return None

            speaker_embeddings = {}
            waveform = audio_data["waveform"]
            sample_rate = audio_data["sample_rate"]

            for speaker in diarization_result.labels():
                # 해당 화자의 가장 긴 세그먼트 찾기
                speaker_segments = [
                    (turn.start, turn.end)
                    for turn, _, spk in diarization_result.itertracks(yield_label=True)
                    if spk == speaker
                ]

                if not speaker_segments:
                    continue

                longest_seg = max(speaker_segments, key=lambda x: x[1] - x[0])
                start_time, end_time = longest_seg

                # 해당 구간의 오디오 추출
                start_sample = int(start_time * sample_rate)
                end_sample = int(end_time * sample_rate)
                segment_waveform = waveform[:, start_sample:end_sample]

                # Embedding 추출
                with torch.inference_mode():
                    if len(segment_waveform.shape) == 2:
                        segment_waveform = segment_waveform.unsqueeze(0)
                    elif len(segment_waveform.shape) == 1:
                        segment_waveform = segment_waveform.unsqueeze(0).unsqueeze(0)

                    embedding = actual_model(segment_waveform)

                    if isinstance(embedding, torch.Tensor):
                        embedding = embedding.cpu().numpy()
                    if isinstance(embedding, np.ndarray):
                        if len(embedding.shape) == 2 and embedding.shape[0] == 1:
                            embedding = embedding[0]
                        elif len(embedding.shape) > 1:
                            embedding = np.mean(embedding, axis=0)
                        embedding = embedding.tolist()

                speaker_embeddings[speaker] = embedding

            return speaker_embeddings if speaker_embeddings else None

        except Exception as e:
            logger.error(f"[DiarizationService] Error extracting embeddings: {e}")
            return None

    def unload_pipeline(self):
        """파이프라인 언로드 및 GPU 메모리 해제."""
        import torch

        if self._pipeline is not None:
            del self._pipeline
            self._pipeline = None

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        logger.info("[DiarizationService] Pipeline unloaded, GPU memory released")


# 싱글톤 인스턴스
_diarization_service: Optional[DiarizationService] = None


def get_diarization_service() -> DiarizationService:
    """싱글톤 DiarizationService 인스턴스."""
    global _diarization_service
    if _diarization_service is None:
        _diarization_service = DiarizationService()
    return _diarization_service
