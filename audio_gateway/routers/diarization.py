"""Diarization Router - 화자분리 API."""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile, Form, HTTPException

from ..config import get_settings
from ..services.diarization_service import get_diarization_service
from ..models.schemas import DiarizationResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/diarization", response_model=DiarizationResponse)
async def create_diarization(
    file: UploadFile = File(...),
    min_speakers: Optional[int] = Form(default=None),
    max_speakers: Optional[int] = Form(default=None),
    return_embeddings: bool = Form(default=False),
) -> DiarizationResponse:
    """
    화자분리 API.

    POST /v1/audio/diarization

    Args:
        file: 오디오 파일 (WAV, MP3, M4A 등)
        min_speakers: 최소 화자 수 (None이면 자동 결정)
        max_speakers: 최대 화자 수 (None이면 자동 결정)
        return_embeddings: 화자별 임베딩 벡터 반환 여부

    Returns:
        DiarizationResponse
    """
    settings = get_settings()
    diarization_service = get_diarization_service()

    logger.info(
        f"[Diarization] Received request: min_speakers={min_speakers}, "
        f"max_speakers={max_speakers}, return_embeddings={return_embeddings}"
    )

    # 임시 디렉토리 확인
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    # 임시 파일로 저장
    temp_path = settings.temp_dir / f"diar_{file.filename}"
    try:
        content = await file.read()
        temp_path.write_bytes(content)

        logger.info(f"[Diarization] Saved to temp file: {temp_path} ({len(content)} bytes)")

        # pyannote로 화자분리
        result = await diarization_service.diarize(
            audio_path=temp_path,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            return_embeddings=return_embeddings,
        )

        logger.info(
            f"[Diarization] Completed: {result.num_speakers} speakers, "
            f"{len(result.segments)} segments"
        )

        return result

    except Exception as e:
        logger.error(f"[Diarization] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 임시 파일 삭제
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception as e:
                logger.warning(f"[Diarization] Failed to delete temp file: {e}")
