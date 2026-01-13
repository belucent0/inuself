"""Audio Gateway Pydantic 모델."""
from pydantic import BaseModel, Field
from typing import Optional, Any


# ============================================================
# Transcription (ASR) 관련 스키마
# ============================================================

class TranscriptionSegment(BaseModel):
    """전사 세그먼트."""
    id: int
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    avg_logprob: Optional[float] = None
    compression_ratio: Optional[float] = None
    no_speech_prob: Optional[float] = None


class TranscriptionResponse(BaseModel):
    """전사 응답 모델 (OpenAI 호환)."""
    text: str
    language: str = "ko"
    duration: Optional[float] = None
    segments: list[TranscriptionSegment] = Field(default_factory=list)


# ============================================================
# Diarization (화자분리) 관련 스키마
# ============================================================

class DiarizationSegment(BaseModel):
    """화자분리 세그먼트."""
    start: float
    end: float
    speaker: str
    duration: float


class DiarizationResponse(BaseModel):
    """화자분리 응답 모델."""
    segments: list[DiarizationSegment] = Field(default_factory=list)
    num_speakers: int
    speaker_labels: list[str] = Field(default_factory=list)
    duration: float
    embeddings: Optional[dict[str, list[float]]] = None  # speaker -> embedding vector

    # 메타데이터
    load_time: Optional[float] = None
    process_time: Optional[float] = None


# ============================================================
# 통합 ASR + Diarization 스키마
# ============================================================

class TranscriptionWithSpeakersSegment(BaseModel):
    """화자 정보가 포함된 전사 세그먼트."""
    id: int
    start: float
    end: float
    text: str
    speaker: str
    duration: float
    overall_confidence: Optional[float] = None


class TranscriptionWithSpeakersResponse(BaseModel):
    """화자분리가 포함된 전사 응답."""
    text: str
    language: str = "ko"
    duration: float
    segments: list[TranscriptionWithSpeakersSegment] = Field(default_factory=list)
    speaker_stats: dict[str, Any] = Field(default_factory=dict)

    # 메타데이터
    asr_time: Optional[float] = None
    diarization_time: Optional[float] = None
    total_time: Optional[float] = None
