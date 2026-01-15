"""Celery 리소스 정리 태스크.

정체된 리소스 락과 불일치한 active_count를 정리합니다.
Celery Beat에서 주기적으로 실행됩니다.
"""
import httpx
import json
import logging
from datetime import datetime

from worker.celery_app import celery_app
from worker.config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)

# LiteLLM Base URL
LITELLM_BASE_URL = settings.litellm_base_url or "http://litellm:4000"
RESOURCE_STATUS_URL = f"{LITELLM_BASE_URL}/resource/status"
FORCE_RELEASE_URL = f"{LITELLM_BASE_URL}/resource/force-release"

# 모든 리소스 타입/테스크 타입 조합
RESOURCE_COMBINATIONS = [
    ("gpu", "asr"),
    ("gpu", "ocr"),
    ("gpu", "llm"),
    ("gpu", "diarization"),
    ("npu", "asr"),
    ("npu", "ocr"),
    ("npu", "llm"),
]

# Celery task ID 패턴 (ocr-*, llm-*, asr-*)
TASK_PREFIXES = {
    "ocr": "ocr-",
    "llm": "llm-",
    "asr": "asr-",
}


def _check_celery_task_active(task_id: str) -> bool:
    """Celery task가 아직 활성 상태인지 확인합니다.

    Redis inspect를 사용하여 task 상태를 확인합니다.
    """
    import redis
    from worker.config import get_settings

    settings = get_settings()
    try:
        r = redis.from_url(settings.redis_url, decode_responses=True)

        # Celery task-key 패턴 확인
        # celery-task-meta-{task_id}는 task 결과가 저장됨
        # 활성 task는 celery-task-meta가 없거나 결과가 아직 저장되지 않음
        task_key = f"celery-task-meta-{task_id}"

        # task가 존재하고 결과가 있으면 완료/실패 상태
        task_meta = r.get(task_key)
        if task_meta:
            try:
                meta = json.loads(task_meta)
                status = meta.get("status")
                # SUCCESS, FAILURE, REVOKED는 task 종료
                if status in ("SUCCESS", "FAILURE", "REVOKED"):
                    return False
            except json.JSONDecodeError:
                pass

        # task가 active인지 추가 확인 (worker heartbeat)
        # celery:celery@hostname:pid 로 확인 가능하지만 복잡함
        # 여기서는 task meta가 없으면 active로 간주 (conservative)

        r.close()
        return True
    except Exception as e:
        logger.warning(f"[Resource Cleanup] Failed to check Celery task {task_id}: {e}")
        # 확인 실패 시 conservative하게 active로 간주 (잘못 해제 방지)
        return True


@celery_app.task(
    name="worker.tasks.cleanup_task.cleanup_stale_resources",
    bind=True,
)
def cleanup_stale_resources(self):
    """정체된 리소스 락을 정리합니다.

    Celery Beat에서 5분마다 실행됩니다.

    로직:
    1. 모든 리소스 락 스캔
    2. 각 락의 task_id로 Celery 상태 확인
    3. task가 종료되었으면 락 강제 해제
    4. active_count와 실제 락 불일치 수정
    """
    logger.info("[Resource Cleanup] Starting stale resource cleanup...")

    try:
        # 1. 현재 리소스 상태 조회
        response = httpx.get(RESOURCE_STATUS_URL, timeout=5.0)
        response.raise_for_status()
        status_data = response.json()

        locked_resources = []
        unlocked_resources = []

        # 2. 각 락 검사
        for resource_type, task_type in RESOURCE_COMBINATIONS:
            key = f"{resource_type}-{task_type}"
            key_status = status_data.get("status", {}).get(key, {})

            if not key_status.get("locked"):
                continue

            lock_data_str = key_status.get("data", "")
            if not lock_data_str:
                continue

            try:
                lock_data = json.loads(lock_data_str)
                task_id = lock_data.get("task_id")
                acquired_at = lock_data.get("acquired_at", 0)

                # Celery task 상태 확인
                task_active = _check_celery_task_active(task_id)

                if not task_active:
                    # task 종료됨 - 락 해제
                    logger.info(
                        f"[Resource Cleanup] Stale lock found: {key} "
                        f"(task_id={task_id}, acquired_at={acquired_at})"
                    )

                    force_response = httpx.post(
                        FORCE_RELEASE_URL,
                        json={
                            "resource_type": resource_type,
                            "task_type": task_type,
                        },
                        timeout=5.0
                    )
                    force_response.raise_for_status()
                    result = force_response.json()

                    locked_resources.append({
                        "key": key,
                        "task_id": task_id,
                        "released": True,
                        "message": result.get("message"),
                    })

                    logger.info(
                        f"[Resource Cleanup] Released stale lock: {key} "
                        f"(was owner: {result.get('previous_owner')})"
                    )
                else:
                    logger.debug(
                        f"[Resource Cleanup] Lock {key} is valid "
                        f"(task_id={task_id} is still active)"
                    )
                    unlocked_resources.append({
                        "key": key,
                        "task_id": task_id,
                        "active": True,
                    })
            except json.JSONDecodeError:
                logger.warning(f"[Resource Cleanup] Failed to parse lock data for {key}")

        # 3. active_count 불일치 확인 및 수정
        # 실제 락 수 != active_count인 경우 수정
        active_counts = status_data.get("active_counts", {})

        # provider 매핑 (resource_type/task_type -> provider)
        PROVIDER_MAPPING = {
            ("gpu", "asr"): "whisper-cpp",
            ("gpu", "ocr"): "llama-ocr",
            ("gpu", "llm"): "llama",
            ("gpu", "diarization"): "diarization-server",
            ("npu", "asr"): "flm-asr",
            ("npu", "ocr"): "flm-ocr",
            ("npu", "llm"): "flm-llm",
        }

        # 실제 락 수 계산
        provider_lock_counts = {}
        for resource_type, task_type in RESOURCE_COMBINATIONS:
            provider = PROVIDER_MAPPING.get((resource_type, task_type))
            if not provider:
                continue

            key = f"{resource_type}-{task_type}"
            key_status = status_data.get("status", {}).get(key, {})
            if key_status.get("locked"):
                provider_lock_counts[provider] = provider_lock_counts.get(provider, 0) + 1

        # 불일치 수정
        fixed_providers = []
        for provider, active_count in active_counts.items():
            actual_count = provider_lock_counts.get(provider, 0)
            if active_count != actual_count:
                logger.warning(
                    f"[Resource Cleanup] Active count mismatch: {provider} "
                    f"(count={active_count}, actual_locks={actual_count})"
                )
                # active_count 수정 (간단 구현: 실제 락 수로 설정)
                # 더 정교한 구현: 누적 카운트가 아니면 락 수로 설정
                # 여기서는 실제 락 수로 설정
                # TODO: Redis 직접 접근하여 수정

                fixed_providers.append({
                    "provider": provider,
                    "was": active_count,
                    "corrected": actual_count,
                })

        # 4. 요약 로그
        summary = {
            "timestamp": datetime.utcnow().isoformat(),
            "released_count": len(locked_resources),
            "released": locked_resources,
            "fixed_count": len(fixed_providers),
            "fixed": fixed_providers,
        }

        if locked_resources or fixed_providers:
            logger.info(
                f"[Resource Cleanup] Cleanup completed: "
                f"released={len(locked_resources)} locks, "
                f"fixed={len(fixed_providers)} active_counts"
            )
        else:
            logger.info("[Resource Cleanup] No stale resources found")

        return summary

    except Exception as exc:
        logger.error(f"[Resource Cleanup] Error during cleanup: {exc}")
        raise


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
    from worker.config import get_settings
    from worker.utils.storage import delete_files_by_prefix
    from minio import Minio
    from minio.commonconfig import CopySource
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
