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


def get_public_media_url(key: str) -> str | None:
    """MinIO에 저장된 파일의 public URL을 반환."""
    settings = get_settings()
    client = get_s3_client()
    if client:
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
            # media_base_url이 설정되어 있으면 nginx 프록시 경로 사용
            if settings.media_base_url:
                # key는 "uploads/xxx.mp4" 형식이므로 그대로 사용
                return f"{settings.media_base_url}/{key}"
            else:
                # 개발 환경: MinIO 직접 URL 생성
                return f"{settings.s3_endpoint}/{settings.s3_bucket}/{key}"
        except ClientError:
            pass
    return None


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
        error_code = exc.response.get("Error", {}).get("Code", "")
        error_msg = str(exc)
        
        # 자격증명 오류인 경우 (MinIO 재시작 등으로 인한 초기화)
        if error_code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied"):
            logger.warning(
                "S3 자격증명 오류 (bucket=%s): %s. MinIO가 재시작되어 초기화되었을 수 있습니다.",
                settings.s3_bucket,
                error_msg,
            )
            return False, f"자격증명 오류: {error_msg}. MinIO 설정을 확인하세요 (기본값: torchdev/torchdev-secret)"
        
        # 404 또는 403이면 버킷이 없는 것으로 간주하고 생성 시도
        if error_code in ("404", "NoSuchBucket", "403", "Forbidden"):
            try:
                logger.info(
                    "버킷이 없습니다. 생성 시도 중: bucket=%s",
                    settings.s3_bucket,
                )
                client.create_bucket(Bucket=settings.s3_bucket)
                logger.info("버킷 생성 성공: bucket=%s", settings.s3_bucket)
                return True, (
                    f"S3 연결 성공 (버킷 자동 생성됨): endpoint={settings.s3_endpoint}, "
                    f"bucket={settings.s3_bucket}, prefix={settings.s3_prefix}"
                )
            except ClientError as create_exc:
                create_error_code = create_exc.response.get("Error", {}).get("Code", "")
                if create_error_code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "AccessDenied"):
                    logger.warning(
                        "버킷 생성 실패 - 자격증명 오류 (bucket=%s): %s",
                        settings.s3_bucket,
                        create_exc,
                    )
                    return False, f"자격증명 오류로 버킷 생성 실패: {create_exc}. MinIO 설정을 확인하세요"
                logger.warning(
                    "버킷 생성 실패 (bucket=%s): %s",
                    settings.s3_bucket,
                    create_exc,
                )
                return False, f"버킷 생성 실패: {create_exc}"
        else:
            logger.warning(
                "S3 연결 실패 (bucket=%s): %s",
                settings.s3_bucket,
                exc,
            )
            return False, str(exc)
