"""Transcription Router - OpenAI 호환 ASR API.

Architecture V4: 모델에 따라 ASR 엔진 선택
- whisper-large-v3: insanely-fast-whisper-rocm (정확도 모드)
- whisper-turbo: whisper.cpp turbo (속도 모드 폴백)
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile, Form, HTTPException

from ..config import get_settings
from ..services.whisper_service import get_whisper_service
from ..services.whisper_cpp_service import get_whisper_cpp_service
from ..models.schemas import TranscriptionResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# 모델별 엔진 매핑
TURBO_MODELS = {"turbo", "whisper-turbo", "whisper-large-v3-turbo", "large-v3-turbo"}
ACCURACY_MODELS = {"whisper", "whisper-large-v3", "large-v3", "openai/whisper-large-v3"}


@router.post("/transcriptions", response_model=TranscriptionResponse)
async def create_transcription(
    file: UploadFile = File(...),
    model: str = Form(default="whisper-large-v3"),
    language: Optional[str] = Form(default=None),
    response_format: str = Form(default="verbose_json"),
    temperature: float = Form(default=0.0),
) -> TranscriptionResponse | dict:
    """
    OpenAI 호환 Audio Transcription API.

    POST /v1/audio/transcriptions

    Args:
        file: 오디오 파일 (WAV, MP3, M4A 등)
        model: 모델명
            - "whisper-large-v3" (정확도): insanely-fast-whisper-rocm
            - "whisper-turbo" (속도): whisper.cpp turbo
        language: 언어 코드 (ko, en, ja 등, None이면 자동 감지)
        response_format: 응답 형식 (json, text, verbose_json)
        temperature: 디코딩 온도 (0.0 ~ 1.0)

    Returns:
        TranscriptionResponse (verbose_json) 또는 dict
    """
    settings = get_settings()

    # 모델에 따라 엔진 선택
    use_whisper_cpp = model.lower() in TURBO_MODELS
    engine_name = "whisper.cpp (turbo)" if use_whisper_cpp else "insanely-fast-whisper-rocm (v3)"

    logger.info(f"[Transcription] Received request: model={model}, engine={engine_name}, language={language}")

    # 임시 디렉토리 확인
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    # 임시 파일로 저장
    temp_path = settings.temp_dir / f"asr_{file.filename}"
    try:
        content = await file.read()
        temp_path.write_bytes(content)

        logger.info(f"[Transcription] Saved to temp file: {temp_path} ({len(content)} bytes)")

        # 엔진 선택 및 전사 실행
        if use_whisper_cpp:
            # whisper.cpp turbo (속도 모드)
            whisper_cpp_service = get_whisper_cpp_service()
            result = await whisper_cpp_service.transcribe(
                audio_path=temp_path,
                language=language,
                model_size="turbo",
            )
        else:
            # insanely-fast-whisper-rocm (정확도 모드)
            whisper_service = get_whisper_service()
            result = await whisper_service.transcribe(
                audio_path=temp_path,
                language=language,
                response_format=response_format,
            )

        logger.info(
            f"[Transcription] Completed ({engine_name}): {len(result.text)} chars, "
            f"{len(result.segments)} segments"
        )

        # 응답 형식에 따라 반환
        if response_format == "text":
            return {"text": result.text}
        elif response_format == "json":
            return {"text": result.text, "language": result.language}
        else:  # verbose_json
            return result

    except Exception as e:
        logger.error(f"[Transcription] Error ({engine_name}): {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # 임시 파일 삭제
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception as e:
                logger.warning(f"[Transcription] Failed to delete temp file: {e}")
