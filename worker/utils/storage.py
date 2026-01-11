"""워커 전용 S3 스토리지 유틸리티.

백엔드에 의존하지 않고 워커에서 직접 S3에 접근합니다.
"""
import json
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from worker.config import get_settings
from worker.logging_config import logger


@lru_cache
def get_s3_client() -> BaseClient | None:
    """S3 클라이언트 반환 (싱글톤)."""
    settings = get_settings()
    try:
        session = boto3.session.Session(
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        return session.client("s3", endpoint_url=settings.s3_endpoint)
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
    """
    settings = get_settings()
    client = get_s3_client()
    
    if client:
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
            destination.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(settings.s3_bucket, key, str(destination))
            logger.debug("[Storage] S3 다운로드 완료: key={}", key)
            return destination
        except ClientError as e:
            logger.warning("[Storage] S3 다운로드 실패, 로컬 확인: {}", e)
    
    # 로컬 파일 시스템 폴백
    local_path = _get_local_storage_path(key)
    if local_path.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, destination)
        return destination
    
    raise FileNotFoundError(f"File not found: {key}")


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
            client.head_bucket(Bucket=settings.s3_bucket)
            client.upload_file(str(source), settings.s3_bucket, key)
            logger.debug("[Storage] S3 업로드 완료: key={}", key)
            return key
        except ClientError as e:
            logger.warning("[Storage] S3 업로드 실패, 로컬 저장: {}", e)
    
    # 로컬 파일 시스템 폴백
    local_path = _get_local_storage_path(key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, local_path)
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
    
    json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    
    if client:
        try:
            client.head_bucket(Bucket=settings.s3_bucket)
            client.put_object(
                Bucket=settings.s3_bucket,
                Key=key,
                Body=json_bytes,
                ContentType="application/json",
            )
            logger.debug("[Storage] S3 JSON 업로드 완료: key={}", key)
            return key
        except ClientError as e:
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
            client.head_bucket(Bucket=settings.s3_bucket)
            response = client.get_object(Bucket=settings.s3_bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            return json.loads(content)
        except ClientError as e:
            logger.warning("[Storage] S3 다운로드 실패, 로컬 확인: {}", e)
    
    # 로컬 파일 시스템 폴백
    local_path = _get_local_storage_path(key)
    if local_path.exists():
        return json.loads(local_path.read_text(encoding="utf-8"))
    
    raise FileNotFoundError(f"File not found: {key}")
