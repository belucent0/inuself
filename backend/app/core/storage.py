from __future__ import annotations

import json
import shutil
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from .config import get_settings
from .logging import logger


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


def wait_for_file(
    key: str,
    *,
    max_attempts: int = 10,
    interval: float = 0.5,
) -> bool:
    """
    S3/스토리지에 파일이 가용 상태가 될 때까지 대기.
    
    업로드 직후 파일이 즉시 가용하지 않을 수 있는 eventual consistency 문제를 해결합니다.
    Celery 태스크 큐잉 전에 호출하여 워커가 파일을 찾지 못하는 race condition을 방지합니다.
    
    Args:
        key: S3 키 (object key)
        max_attempts: 최대 시도 횟수 (기본값: 10)
        interval: 시도 간 대기 시간 (초, 기본값: 0.5)
    
    Returns:
        True: 파일이 가용 상태
        False: 최대 시도 후에도 파일을 찾지 못함
    """
    settings = get_settings()
    
    for attempt in range(max_attempts):
        # S3 확인
        client = get_s3_client()
        if client:
            try:
                client.head_object(Bucket=settings.s3_bucket, Key=key)
                if attempt > 0:
                    logger.info(
                        "[Storage] 파일 가용 확인 완료 (시도 %d/%d): key=%s",
                        attempt + 1,
                        max_attempts,
                        key,
                    )
                return True
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "404":
                    # 파일이 아직 없음, 재시도
                    if attempt < max_attempts - 1:
                        logger.debug(
                            "[Storage] 파일 대기 중 (시도 %d/%d): key=%s",
                            attempt + 1,
                            max_attempts,
                            key,
                        )
                        time.sleep(interval)
                        continue
                else:
                    # 다른 에러는 로그 후 로컬 확인으로 넘어감
                    logger.warning("[Storage] S3 head_object 실패: %s", e)
        
        # 로컬 파일 시스템 확인
        local_path = _get_local_storage_path(key)
        if local_path.exists():
            return True
        
        if attempt < max_attempts - 1:
            time.sleep(interval)
    
    logger.warning(
        "[Storage] 파일 가용 확인 실패 (최대 시도 초과): key=%s, attempts=%d",
        key,
        max_attempts,
    )
    return False


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


def get_public_media_url(key: str) -> str:
    """
    파일의 public URL을 반환.
    복잡한 확인 로직 없이 설정된 Base URL 또는 기본값(/media)을 사용하여 URL을 생성합니다.
    """
    settings = get_settings()
    # 설정된 값이 없으면 기본값 "/media" 사용 (로컬/Docker 공통)
    base_url = settings.media_base_url or "/media"
    # 경로 끝에 /가 있으면 제거하여 중복 방지
    base_url = base_url.rstrip("/")
    return f"{base_url}/{key}"


def download_json(key: str) -> dict[str, Any]:
    """S3에서 JSON 파일을 다운로드하고 파싱합니다."""
    settings = get_settings()
    
    client = get_s3_client()
    if client:
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
            response = client.get_object(Bucket=settings.s3_bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            return json.loads(content)
        except ClientError as e:
            logger.warning(f"S3 download failed, trying local: {e}")
    
    # 로컬 파일 시스템 폴백
    local_path = _get_local_storage_path(key)
    if local_path.exists():
        return json.loads(local_path.read_text(encoding="utf-8"))
    
    raise FileNotFoundError(f"File not found: {key}")


def delete_files_by_prefix(prefix: str) -> int:
    """지정된 prefix로 시작하는 모든 파일을 삭제합니다.
    
    Returns:
        삭제된 파일 수
    """
    settings = get_settings()
    deleted_count = 0
    
    client = get_s3_client()
    if client:
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
            
            # prefix로 시작하는 모든 객체 나열
            paginator = client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix)
            
            objects_to_delete = []
            for page in pages:
                for obj in page.get("Contents", []):
                    objects_to_delete.append({"Key": obj["Key"]})
            
            if objects_to_delete:
                # 한 번에 최대 1000개까지 삭제 가능
                for i in range(0, len(objects_to_delete), 1000):
                    batch = objects_to_delete[i:i + 1000]
                    client.delete_objects(
                        Bucket=settings.s3_bucket,
                        Delete={"Objects": batch}
                    )
                    deleted_count += len(batch)
            
            logger.info(f"Deleted {deleted_count} objects with prefix: {prefix}")
            return deleted_count
            
        except ClientError as e:
            logger.warning(f"S3 delete by prefix failed: {e}")
    
    # 로컬 파일 시스템 폴백
    storage_dir = settings.upload_dir / "storage"
    prefix_normalized = prefix.replace("/", "_")
    for path in storage_dir.glob(f"{prefix_normalized}*"):
        if path.is_file():
            path.unlink()
            deleted_count += 1
    
    return deleted_count


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
