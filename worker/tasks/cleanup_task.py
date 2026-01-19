"""Celery 정리 태스크.

Architecture V6.5: 리소스 게이트 제거됨 - LiteLLM Custom Handler가 직접 라우팅 및 메모리 관리
임시 파일 정리 태스크만 유지됩니다.
"""
import logging
from datetime import datetime

from worker.celery_app import celery_app
from worker.config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)


@celery_app.task(
    name="worker.tasks.cleanup_task.cleanup_old_temp_files",
    bind=True,
)
def cleanup_old_temp_files(self):
    """7일 지난 임시 파일을 정리합니다.

    Celery Beat에서 1시간마다 실행됩니다.

    로직:
    1. S3에서 temp/ocr/, temp/asr/ 디렉토리 스캔
    2. 파일 생성일 확인
    3. 7일 지난 파일 삭제
    """
    from minio import Minio
    from datetime import datetime, timedelta

    settings = get_settings()
    logger.info("[Temp File Cleanup] Starting old temp file cleanup...")

    try:
        # MinIO 클라이언트 생성
        minio_client = Minio(
            settings.s3_endpoint.replace("http://", "").replace("https://", ""),
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=settings.s3_endpoint.startswith("https://"),
        )

        cutoff_date = datetime.utcnow() - timedelta(days=7)
        deleted_count = 0
        total_checked = 0

        # temp/ocr/ 및 temp/asr/ 디렉토리 스캔
        temp_prefixes = ["temp/ocr/", "temp/asr/"]

        for prefix in temp_prefixes:
            logger.info(f"[Temp File Cleanup] Scanning {prefix}...")

            try:
                # MinIO ListObjectsV2 사용
                objects = minio_client.list_objects(
                    settings.s3_bucket,
                    prefix=prefix,
                    recursive=True,
                )

                for obj in objects:
                    total_checked += 1

                    # 파일 생성일 확인 (last_modified)
                    if obj.last_modified < cutoff_date:
                        # 파일 삭제
                        try:
                            minio_client.remove_object(settings.s3_bucket, obj.object_name)
                            logger.info(
                                f"[Temp File Cleanup] Deleted old file: {obj.object_name} "
                                f"(created: {obj.last_modified}, age: {(datetime.utcnow() - obj.last_modified).days} days)"
                            )
                            deleted_count += 1
                        except Exception as e:
                            logger.warning(
                                f"[Temp File Cleanup] Failed to delete {obj.object_name}: {e}"
                            )

            except Exception as e:
                logger.error(f"[Temp File Cleanup] Error scanning {prefix}: {e}")

        # 요약
        summary = {
            "timestamp": datetime.utcnow().isoformat(),
            "total_checked": total_checked,
            "deleted_count": deleted_count,
            "cutoff_date": cutoff_date.isoformat(),
        }

        if deleted_count > 0:
            logger.info(
                f"[Temp File Cleanup] Cleanup completed: "
                f"checked={total_checked}, deleted={deleted_count} files"
            )
        else:
            logger.info(
                f"[Temp File Cleanup] Cleanup completed: "
                f"checked={total_checked}, no files to delete (cutoff: {cutoff_date.date()})"
            )

        return summary

    except Exception as exc:
        logger.error(f"[Temp File Cleanup] Error during cleanup: {exc}")
        raise
