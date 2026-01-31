"""워커 전용 S3 스토리지 유틸리티.

백엔드에 의존하지 않고 워커에서 직접 S3에 접근합니다.
"""
import json
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID


class UUIDEncoder(json.JSONEncoder):
    """UUID 객체를 JSON으로 직렬화하는 인코더."""

    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)

from minio import Minio
from minio.error import S3Error

from worker.config import get_settings
from worker.logging_config import logger


@lru_cache
def get_s3_client() -> Minio | None:
    """S3 클라이언트 반환 (싱글톤, MinIO SDK)."""
    settings = get_settings()
    try:
        # endpoint에서 http:// 또는 https:// 제거
        endpoint = settings.s3_endpoint.replace("http://", "").replace("https://", "")
        secure = settings.s3_endpoint.startswith("https://")

        return Minio(
            endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            secure=secure,
        )
    except Exception as e:
        logger.warning("[Storage] S3 클라이언트 생성 실패: {}", e)
        return None


def _get_local_storage_path(key: str) -> Path:
    """로컬 파일 시스템 저장 경로 반환."""
    settings = get_settings()
    storage_dir = settings.temp_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir / key.replace("/", "_")


def download_file(key: str, *, destination: Path) -> Path:
    """S3에서 파일 다운로드.
    
    Args:
        key: S3 object key
        destination: 로컬 저장 경로
        
    Returns:
        다운로드된 파일 경로
        
    Raises:
        FileNotFoundError: S3와 로컬 모두에서 파일을 찾을 수 없을 때
    """
    settings = get_settings()
    client = get_s3_client()
    
    if client:
        try:
            # 버킷 접근 가능 여부 확인
            if not client.bucket_exists(settings.s3_bucket):
                raise S3Error("Bucket does not exist", None, None, None, None, None)

            # 파일 존재 여부 먼저 확인
            try:
                client.stat_object(settings.s3_bucket, key)
            except S3Error as head_err:
                if head_err.code == "NoSuchKey" or head_err.code == "404":
                    logger.warning(
                        "[Storage] S3 파일 없음 (404): key={}, bucket={}, endpoint={}",
                        key, settings.s3_bucket, settings.s3_endpoint
                    )
                else:
                    logger.warning(
                        "[Storage] S3 파일 확인 실패: key={}, error={}",
                        key, head_err
                    )
                # stat_object 실패 시 로컬 폴백으로 진행
            else:
                # 파일이 존재하면 다운로드
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    client.fget_object(settings.s3_bucket, key, str(destination))
                    logger.debug("[Storage] S3 다운로드 완료: key={}", key)
                    return destination
                except S3Error as download_err:
                    logger.warning(
                        "[Storage] S3 다운로드 실패: key={}, error={}",
                        key, download_err
                    )
                    # 다운로드 실패 시 로컬 폴백으로 진행
        except S3Error as e:
            if e.code == "NoSuchBucket" or e.code == "404":
                logger.warning(
                    "[Storage] S3 버킷 접근 실패 또는 파일 없음: bucket={}, key={}, error={}",
                    settings.s3_bucket, key, e
                )
            else:
                logger.warning(
                    "[Storage] S3 접근 실패, 로컬 확인: key={}, error={}",
                    key, e
                )
        except Exception as e:
            logger.warning(
                "[Storage] S3 처리 중 예외 발생, 로컬 확인: key={}, error={}",
                key, e
            )
    
    # 로컬 파일 시스템 폴백
    local_path = _get_local_storage_path(key)
    if local_path.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, destination)
        logger.debug("[Storage] 로컬 파일 사용: key={}", key)
        return destination
    
    # S3와 로컬 모두에서 파일을 찾을 수 없음
    raise FileNotFoundError(
        f"File not found: {key} (S3 bucket: {settings.s3_bucket}, "
        f"endpoint: {settings.s3_endpoint}, local path: {local_path})"
    )


def upload_file(source: Path, *, key: str) -> str:
    """파일을 S3에 업로드.

    Args:
        source: 로컬 파일 경로
        key: S3 object key

    Returns:
        업로드된 S3 key
    """
    settings = get_settings()
    client = get_s3_client()

    if client:
        try:
            # 버킷 존재 확인
            if not client.bucket_exists(settings.s3_bucket):
                raise S3Error("Bucket does not exist", None, None, None, None, None)

            client.fput_object(settings.s3_bucket, key, str(source))
            logger.debug("[Storage] S3 업로드 완료: key={}", key)
            return key
        except S3Error as e:
            logger.warning("[Storage] S3 업로드 실패, 로컬 저장: {}", e)

    # 로컬 파일 시스템 폴백
    local_path = _get_local_storage_path(key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, local_path)
    logger.debug("[Storage] 로컬 저장 완료: path={}", local_path)
    return key


def upload_fileobj(file_obj: BinaryIO, *, key: str) -> str:
    """파일 객체를 S3에 업로드.

    Args:
        file_obj: 업로드할 파일 객체 (BinaryIO)
        key: S3 object key

    Returns:
        업로드된 S3 key
    """
    settings = get_settings()
    client = get_s3_client()

    if client:
        try:
            # 버킷 존재 확인
            if not client.bucket_exists(settings.s3_bucket):
                raise S3Error("Bucket does not exist", None, None, None, None, None)

            # 파일 크기 확인 (minio는 크기 필요)
            file_obj.seek(0, 2)  # EOF로 이동
            file_size = file_obj.tell()
            file_obj.seek(0)  # 처음으로 리셋

            client.put_object(settings.s3_bucket, key, file_obj, file_size)
            logger.debug("[Storage] S3 업로드 완료: key={}", key)
            return key
        except S3Error as e:
            logger.warning("[Storage] S3 업로드 실패, 로컬 저장: {}", e)

    # 로컬 파일 시스템 폴백
    local_path = _get_local_storage_path(key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    file_obj.seek(0)
    local_path.write_bytes(file_obj.read())
    logger.debug("[Storage] 로컬 저장 완료: path={}", local_path)
    return key


def upload_json(data: dict[str, Any], *, key: str) -> str:
    """JSON 데이터를 S3에 업로드.

    Args:
        data: JSON으로 직렬화할 데이터
        key: S3 object key

    Returns:
        업로드된 S3 key
    """
    settings = get_settings()
    client = get_s3_client()

    json_bytes = json.dumps(data, ensure_ascii=False, indent=2, cls=UUIDEncoder).encode("utf-8")

    if client:
        try:
            # 버킷 존재 확인
            if not client.bucket_exists(settings.s3_bucket):
                raise S3Error("Bucket does not exist", None, None, None, None, None)

            import io
            client.put_object(
                settings.s3_bucket,
                key,
                io.BytesIO(json_bytes),
                len(json_bytes),
                content_type="application/json",
            )
            logger.debug("[Storage] S3 JSON 업로드 완료: key={}", key)
            return key
        except S3Error as e:
            logger.warning("[Storage] S3 업로드 실패, 로컬 저장: {}", e)

    # 로컬 파일 시스템 폴백
    local_path = _get_local_storage_path(key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(json_bytes)
    logger.debug("[Storage] 로컬 JSON 저장 완료: path={}", local_path)
    return key


def download_json(key: str) -> dict[str, Any]:
    """S3에서 JSON 파일 다운로드 및 파싱.

    Args:
        key: S3 object key

    Returns:
        파싱된 JSON 데이터
    """
    settings = get_settings()
    client = get_s3_client()

    if client:
        try:
            # 버킷 존재 확인
            if not client.bucket_exists(settings.s3_bucket):
                raise S3Error("Bucket does not exist", None, None, None, None, None)

            response = client.get_object(settings.s3_bucket, key)
            content = response.read().decode("utf-8")
            response.close()
            response.release_conn()
            return json.loads(content)
        except S3Error as e:
            logger.warning("[Storage] S3 다운로드 실패, 로컬 확인: {}", e)

    # 로컬 파일 시스템 폴백
    local_path = _get_local_storage_path(key)
    if local_path.exists():
        return json.loads(local_path.read_text(encoding="utf-8"))

    raise FileNotFoundError(f"File not found: {key}")
