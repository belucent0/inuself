"""OCR (문서 인식) 처리 프로세서.

이 모듈은 파일에서 텍스트를 추출합니다.
- 전처리(PDF/Office → 이미지 변환)는 Worker에서 수행
- OCR 추론은 ai-gateway → ai-ocr 컨테이너(dots.ocr) httpx 직결 호출
- 결과는 S3에 저장하고 stream:worker:results로 완료 알림 (백엔드가 consume)
"""
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import uuid4

from PIL import Image

from worker.config import get_settings
from worker.logging_config import logger
from worker.utils.event_loop import setup_worker_event_loop, cleanup_worker_event_loop
from worker.utils.storage import download_file, upload_json
from worker.utils.event_publisher import publish_file_progress
from worker.utils.result_publisher import (
    publish_ocr_started,
    publish_ocr_completed,
    publish_ocr_failed,
)
from worker.pipelines.ocr.vision import OcrVisionProcessor
from worker.pipelines.ocr.preprocessor import OcrPreprocessor

settings = get_settings()

OcrMode = Literal["document", "portray"]


def process_ocr_job(
    *,
    file_id: int,
    file_s3_key: str | None = None,
    image_s3_keys: list[str] | None = None,
    ocr_mode: OcrMode = "document",
    ocr_accuracy_mode: str = "speed",
) -> None:
    """Celery 워커가 호출하는 OCR 작업 진입점.

    Args:
        file_id: 파일 ID
        file_s3_key: 원본 파일 S3 경로 (새 방식, Worker 전처리)
        image_s3_keys: 이미지 S3 경로 목록 (기존 방식, Backend 전처리)
        ocr_mode: OCR 모드 ("document" 또는 "portray")
        ocr_accuracy_mode: OCR 정확도 모드 ("speed" 또는 "accuracy")
    """
    logger.info("[OCR] ========================================")
    logger.info(f"[OCR] OCR job started: file_id={file_id}")
    if file_s3_key:
        logger.info(f"[OCR] Mode: Worker preprocessing (file_s3_key={file_s3_key})")
    elif image_s3_keys:
        logger.info(f"[OCR] Mode: Backend preprocessing (image count={len(image_s3_keys)})")
    logger.info(f"[OCR] OCR mode: {ocr_mode}")
    logger.info(f"[OCR] Accuracy mode: {ocr_accuracy_mode}")
    logger.info("[OCR] ========================================")

    # 이벤트 루프 설정
    loop = setup_worker_event_loop()

    try:
        loop.run_until_complete(
            _process_job(
                file_id=file_id,
                file_s3_key=file_s3_key,
                image_s3_keys=image_s3_keys,
                ocr_mode=ocr_mode,
                ocr_accuracy_mode=ocr_accuracy_mode,
            )
        )
        logger.info(f"[OCR] OK OCR job completed: file_id={file_id}")
    except Exception as e:
        logger.error(f"[OCR] ERROR OCR job failed: file_id={file_id}, error={e}")
        raise
    finally:
        cleanup_worker_event_loop(loop)


async def _process_job(
    *,
    file_id: int,
    file_s3_key: str | None = None,
    image_s3_keys: list[str] | None = None,
    ocr_mode: OcrMode = "document",
    ocr_accuracy_mode: str = "speed",
) -> None:
    """OCR 작업 처리 함수."""
    file_id_str = str(file_id)

    # 파라미터 검증
    if not file_s3_key and not image_s3_keys:
        raise ValueError("Either file_s3_key or image_s3_keys must be provided")

    # Redis Stream: 처리 시작 이벤트 발행
    publish_ocr_started(file_id)
    logger.info(f"[OCR] Published processing_started event: file_id={file_id}")

    if file_s3_key:
        logger.info(f"[OCR] [1/5] Starting OCR job (Worker preprocessing): file_id={file_id}, file_s3_key={file_s3_key}")
    else:
        logger.info(f"[OCR] [1/4] Starting OCR job (Backend preprocessing): file_id={file_id}, images={len(image_s3_keys)}")
        logger.info(
            "[OCR] Received image S3 keys: file_id=%s, keys=%s, endpoint=%s, bucket=%s",
            file_id, image_s3_keys, settings.s3_endpoint, settings.s3_bucket
        )

    # OCR provider 결정 (리소스 획득 전에 필요)
    if ocr_accuracy_mode == "speed":
        ocr_provider = "flm"
    elif ocr_accuracy_mode == "accuracy":
        ocr_provider = "llamacpp_server"
    else:
        # 기본값은 speed (flm)
        logger.warning(f"[OCR] Unknown ocr_accuracy_mode: {ocr_accuracy_mode}, using default 'speed' (flm)")
        ocr_provider = "flm"

    logger.info(f"[OCR] OCR accuracy mode: {ocr_accuracy_mode}, selected provider: {ocr_provider}")

    ocr_processor = OcrVisionProcessor(ocr_provider=ocr_provider)
    logger.info(f"[OCR] OcrVisionProcessor created with provider override: {ocr_processor._ocr_provider_override}")

    images: list[Image.Image] = []
    temp_dir = settings.temp_dir / f"ocr_{file_id}_{uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    is_text_file = False
    text_content = None

    try:
        # 새 방식: 원본 파일 처리 (Worker 전처리)
        if file_s3_key:
            logger.info("[OCR] [2/5] Downloading original file from S3...")

            # 1. 원본 파일 다운로드
            file_suffix = Path(file_s3_key).suffix
            original_file_path = temp_dir / f"original{file_suffix}"
            download_file(file_s3_key, destination=original_file_path)
            logger.info(f"[OCR] Original file downloaded: {original_file_path}")
            publish_file_progress(file_id_str, "OCR_PROCESSING", "download_complete", 8, "파일 다운로드 완료")

            # 2. 전처리: 파일 → 이미지 변환
            logger.info("[OCR] [3/5] Converting file to images...")
            preprocessor = OcrPreprocessor()
            prep_result = preprocessor.convert_to_images(original_file_path, file_id)

            is_text_file = prep_result.get("is_text_file", False)
            text_content = prep_result.get("text_content")

            if is_text_file:
                logger.info("[OCR] Text file detected, OCR not required")
            else:
                images = prep_result["images"]
                logger.info(f"[OCR] [4/5] Converted to {len(images)} images, acquiring OCR resource...")
                publish_file_progress(file_id_str, "OCR_PROCESSING", "images_ready", 20, "이미지 변환 완료", metadata={"page_count": len(images)})

        # 기존 방식: 이미지 다운로드 (Backend 전처리)
        else:
            logger.info("[OCR] [2/4] Downloading images from S3...")

            for idx, s3_key in enumerate(image_s3_keys):
                temp_path = temp_dir / f"page_{idx + 1}.jpg"
                try:
                    logger.debug(
                        "[OCR] Download attempt: %d/%d - key=%s",
                        idx + 1, len(image_s3_keys), s3_key
                    )
                    download_file(s3_key, destination=temp_path)

                    if not temp_path.exists():
                        raise FileNotFoundError(
                            f"File does not exist after download: {temp_path}"
                        )

                    image = Image.open(temp_path)
                    images.append(image)
                    logger.debug(
                        "[OCR] Image download completed: %d/%d - key=%s, size=%s",
                        idx + 1, len(image_s3_keys), s3_key, image.size
                    )
                except FileNotFoundError as fnf_err:
                    logger.error(
                        "[OCR] File download failed: %d/%d - key=%s, error=%s",
                        idx + 1, len(image_s3_keys), s3_key, fnf_err
                    )
                    raise FileNotFoundError(
                        f"Image file not found: {s3_key} "
                        f"(file_id={file_id}, page={idx + 1}/{len(image_s3_keys)})"
                    ) from fnf_err

            logger.info(f"[OCR] [3/4] Downloaded {len(images)} images, acquiring OCR resource...")
            publish_file_progress(file_id_str, "OCR_PROCESSING", "images_ready", 20, "이미지 다운로드 완료", metadata={"page_count": len(images)})

        # 텍스트 파일은 OCR 불필요
        if is_text_file and text_content is not None:
            logger.info(f"[OCR] [5/5] Text/Office file processing completed: {len(text_content)} chars")

            file_type = prep_result.get("file_type", ".txt")
            html_content = prep_result.get("html_content")  # HTML 결과 가져오기

            # 결과 저장
            result_data = {
                "file_id": file_id,
                "ocr_text": text_content,
                "html_content": html_content,  # DB 저장을 위해 HTML 포함
                "page_count": 1,
                "ocr_metadata": {"file_type": file_type, "direct_read": True},
            }

            result_s3_key = f"results/ocr/{file_id}/{uuid4().hex}.json"
            upload_json(result_data, key=result_s3_key)

            logger.info(f"[OCR] Results saved to S3: {result_s3_key}")

            # Redis Stream: 완료 알림
            publish_ocr_completed(
                file_id,
                result_s3_key=result_s3_key,
                page_count=1,
                text_length=len(text_content),
            )

            logger.info("[OCR] OK Text file processing completed")
            return

        # OCR 처리 (Provider Manager에서 processing_started 이벤트 발행)
        publish_file_progress(file_id_str, "OCR_PROCESSING", "ocr_start", 25, "OCR 처리 시작")

        def _on_ocr_progress(progress: float, message: str) -> None:
            publish_file_progress(file_id_str, "OCR_PROCESSING", "ocr_page", progress, message)

        result = ocr_processor.process_images(
            images,
            ocr_mode=ocr_mode,
            file_id=str(file_id),
            on_progress=_on_ocr_progress,
        )

        step_num = "[5/5]" if file_s3_key else "[4/4]"
        logger.info(f"[OCR] {step_num} OCR completed: {len(result['ocr_text'])} chars extracted")
        publish_file_progress(file_id_str, "OCR_PROCESSING", "ocr_complete", 85, "OCR 처리 완료")
        
    except Exception as exc:
        logger.error(
            "[OCR] ERROR Error occurred: file_id={}, error={}",
            file_id, exc
        )
        logger.exception("OCR processing failed for file_id={}", file_id)
        
        # Redis Stream: 실패 알림
        publish_ocr_failed(file_id, error=str(exc))
        raise
    finally:
        # 이미지 메모리 정리
        for img in images:
            try:
                img.close()
            except Exception:
                pass
        
        # 임시 파일 정리
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
    
    # 결과를 S3에 JSON으로 저장
    logger.info("[OCR] Saving results to S3...")
    
    result_data = {
        "file_id": file_id,
        "ocr_text": result["ocr_text"],
        "page_count": result["page_count"],
        "ocr_metadata": result["ocr_metadata"],
    }
    
    result_s3_key = f"results/ocr/{file_id}/{uuid4().hex}.json"
    upload_json(result_data, key=result_s3_key)

    logger.info(f"[OCR] Results saved to S3: {result_s3_key}")
    publish_file_progress(file_id_str, "OCR_PROCESSING", "s3_upload_complete", 92, "결과 저장 완료")

    # Redis Stream: 완료 알림
    publish_ocr_completed(
        file_id,
        result_s3_key=result_s3_key,
        page_count=result["page_count"],
        text_length=len(result["ocr_text"]),
    )
    
    logger.info("[OCR] OK OCR processing completed, result published to stream")
    logger.info("OCR processing completed for file_id={}", file_id)
