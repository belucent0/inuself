"""OCR (문서 인식) 처리 프로세서.

이 모듈은 이미지에서 텍스트를 추출합니다.
이미지 전처리(PDF → 이미지 변환)는 백엔드에서 수행합니다.
결과는 S3에 저장하고 Redis Stream으로 완료를 알립니다.
백엔드 DB에 직접 접근하지 않습니다.
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
from worker.utils.result_publisher import (
    publish_ocr_started,
    publish_ocr_completed,
    publish_ocr_failed,
)
from worker.pipelines.ocr.vision import OcrVisionProcessor

settings = get_settings()

OcrMode = Literal["document", "portray"]


def process_ocr_job(
    *,
    file_id: int,
    image_s3_keys: list[str],
    ocr_mode: OcrMode = "document",
    ocr_accuracy_mode: str = "speed",
) -> None:
    """Celery 워커가 호출하는 OCR 작업 진입점.
    
    Args:
        file_id: 파일 ID
        image_s3_keys: 이미지 S3 경로 목록 (백엔드에서 전처리된 이미지들)
        ocr_mode: OCR 모드 ("document" 또는 "portray")
        ocr_accuracy_mode: OCR 정확도 모드 ("speed" 또는 "accuracy")
    """
    logger.info("[OCR] ========================================")
    logger.info(f"[OCR] OCR job started: file_id={file_id}")
    logger.info(f"[OCR] Image count: {len(image_s3_keys)}")
    logger.info(f"[OCR] Mode: {ocr_mode}")
    logger.info(f"[OCR] Accuracy mode: {ocr_accuracy_mode}")
    logger.info("[OCR] ========================================")
    
    # 이벤트 루프 설정
    loop = setup_worker_event_loop()
    
    try:
        loop.run_until_complete(
            _process_job(
                file_id=file_id,
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
    image_s3_keys: list[str],
    ocr_mode: OcrMode = "document",
    ocr_accuracy_mode: str = "speed",
) -> None:
    """OCR 작업 처리 함수."""
    logger.info(f"[OCR] [1/4] Starting OCR job: file_id={file_id}, images={len(image_s3_keys)}")
    logger.info(
        "[OCR] Received image S3 keys: file_id=%s, keys=%s, endpoint=%s, bucket=%s",
        file_id, image_s3_keys, settings.s3_endpoint, settings.s3_bucket
    )
    
    # Redis Stream: 작업 시작 알림
    publish_ocr_started(file_id)
    
    # 이미지 다운로드
    logger.info("[OCR] [2/4] Downloading images from S3...")
    
    images: list[Image.Image] = []
    temp_dir = settings.temp_dir / f"ocr_{file_id}_{uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    try:
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
        
        logger.info(f"[OCR] [3/4] Downloaded {len(images)} images, starting OCR...")
        
        # OCR 처리
        # ocr_accuracy_mode에 따라 ocr_provider 결정
        # speed -> flm (NPU), accuracy -> llamacpp_server (GPU/CPU)
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
        result = ocr_processor.process_images(images, ocr_mode=ocr_mode)
        
        logger.info(f"[OCR] [4/4] OCR completed: {len(result['ocr_text'])} chars extracted")
        
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
    
    # Redis Stream: 완료 알림
    publish_ocr_completed(
        file_id,
        result_s3_key=result_s3_key,
        page_count=result["page_count"],
        text_length=len(result["ocr_text"]),
    )
    
    logger.info("[OCR] OK OCR processing completed, result published to stream")
    logger.info("OCR processing completed for file_id={}", file_id)
