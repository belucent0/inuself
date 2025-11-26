from __future__ import annotations

import shutil
import logging
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from .config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_s3_client() -> BaseClient | None:
    """S3 클라이언트 반환."""
    settings = get_settings()
    try:
        session = boto3.session.Session(
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        return session.client("s3", endpoint_url=settings.s3_endpoint)
    except Exception:
        return None


def _get_local_storage_path(key: str) -> Path:
    """로컬 파일 시스템 저장 경로 반환."""
    settings = get_settings()
    storage_dir = settings.upload_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir / key.replace("/", "_")


def upload_fileobj(file_obj: BinaryIO, *, key: str) -> None:
    """파일을 스토리지에 업로드 (로컬 파일 시스템 또는 S3)."""
    settings = get_settings()
    
    # S3 클라이언트가 있고 버킷이 존재하면 S3 사용
    client = get_s3_client()
    if client:
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
            # 버킷이 존재하면 S3 사용
            client.upload_fileobj(file_obj, settings.s3_bucket, key)
            logger.info(
                "[Storage] S3 업로드 완료: endpoint=%s, bucket=%s, key=%s",
                settings.s3_endpoint,
                settings.s3_bucket,
                key,
            )
            return
        except ClientError:
            # 버킷이 없거나 접근 불가능하면 로컬 파일 시스템 사용
            pass
    
    # 로컬 파일 시스템 사용
    local_path = _get_local_storage_path(key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    file_obj.seek(0)
    with open(local_path, "wb") as f:
        shutil.copyfileobj(file_obj, f)


def download_file(key: str, *, destination: Path) -> Path:
    """스토리지에서 파일 다운로드 (로컬 파일 시스템 또는 S3)."""
    settings = get_settings()
    
    # S3 클라이언트가 있고 버킷이 존재하면 S3 사용
    client = get_s3_client()
    if client:
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
            # 버킷이 존재하면 S3 사용
            destination.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(settings.s3_bucket, key, str(destination))
            return destination
        except ClientError:
            # 버킷이 없거나 접근 불가능하면 로컬 파일 시스템 사용
            pass
    
    # 로컬 파일 시스템 사용
    local_path = _get_local_storage_path(key)
    if local_path.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, destination)
        return destination
    else:
        raise FileNotFoundError(f"File not found: {key}")


def delete_file(key: str) -> None:
    """스토리지에서 파일 삭제 (로컬 파일 시스템 또는 S3)."""
    settings = get_settings()
    
    # S3 클라이언트가 있고 버킷이 존재하면 S3 사용
    client = get_s3_client()
    if client:
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
            # 버킷이 존재하면 S3에서 삭제
            try:
                client.delete_object(Bucket=settings.s3_bucket, Key=key)
                return
            except ClientError as e:
                # 파일이 없어도 에러를 발생시키지 않음 (이미 삭제된 경우)
                if e.response.get("Error", {}).get("Code") != "NoSuchKey":
                    raise
                return
        except ClientError:
            # 버킷이 없거나 접근 불가능하면 로컬 파일 시스템 사용
            pass
    
    # 로컬 파일 시스템 사용
    local_path = _get_local_storage_path(key)
    if local_path.exists():
        local_path.unlink()


def check_storage_health() -> tuple[bool, str]:
    """스토리지 연결 상태를 확인하고 메시지를 반환."""
    settings = get_settings()
    client = get_s3_client()
    if client is None:
        local_path = settings.upload_dir / "storage"
        return False, f"S3 클라이언트 생성 실패. 로컬 스토리지 사용: {local_path}"
    
    ok, message = _try_head_bucket(client, settings)
    if ok:
        return True, message
    
    local_path = settings.upload_dir / "storage"
    return False, (
        f"S3 연결 실패 ({message}). bucket={settings.s3_bucket}. "
        f"로컬 스토리지 사용 경로: {local_path}"
    )


def _try_head_bucket(client: BaseClient, settings) -> tuple[bool, str]:
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
        return True, (
            f"S3 연결 성공: endpoint={settings.s3_endpoint}, "
            f"bucket={settings.s3_bucket}, prefix={settings.s3_prefix}"
        )
    except ClientError as exc:
        logger.warning(
            "S3 연결 실패 (bucket=%s): %s",
            settings.s3_bucket,
            exc,
        )
        return False, str(exc)
