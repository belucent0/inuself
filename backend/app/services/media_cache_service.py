"""미디어 파일 로컬 캐시 서비스.

보안이 중요한 미디어 파일을 위한 백엔드 프록시 캐시.
- Cookie session 인증 후에만 접근 가능
- TTL 기반 자동 정리
- Range 요청 지원 (영상 탐색)
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import AsyncGenerator

import aiofiles
import aiofiles.os
from loguru import logger

from ..core.config import get_settings
from ..core.storage import get_s3_client


# 캐시 설정
CACHE_TTL_SECONDS = 3600 * 3  # 3시간
CACHE_MAX_SIZE_MB = 10 * 1024  # 10GB
CHUNK_SIZE = 1024 * 1024  # 1MB 청크


class MediaCacheService:
    """미디어 파일 로컬 캐시 관리."""

    def __init__(self):
        self.settings = get_settings()
        self.cache_dir = self.settings.upload_dir / "media_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_lock = asyncio.Lock()

    def _get_cache_path(self, object_key: str) -> Path:
        """object_key에 대한 캐시 파일 경로 반환."""
        # object_key의 /를 _로 변환하여 평탄화
        safe_key = object_key.replace("/", "_").replace("\\", "_")
        return self.cache_dir / safe_key

    def _get_meta_path(self, cache_path: Path) -> Path:
        """캐시 메타데이터 파일 경로."""
        return cache_path.with_suffix(cache_path.suffix + ".meta")

    async def get_file_info(self, object_key: str) -> dict | None:
        """파일 정보 조회 (캐시 또는 S3)."""
        cache_path = self._get_cache_path(object_key)

        # 캐시에 있으면 캐시 정보 반환
        if cache_path.exists():
            stat = cache_path.stat()
            # TTL 확인
            if time.time() - stat.st_mtime < CACHE_TTL_SECONDS:
                return {
                    "size": stat.st_size,
                    "path": cache_path,
                    "cached": True,
                }
            else:
                # 만료된 캐시 삭제
                await self._delete_cache_file(cache_path)

        # S3에서 정보 조회
        client = get_s3_client()
        if not client:
            return None

        try:
            stat = client.stat_object(self.settings.s3_bucket, object_key)
            return {
                "size": stat.size,
                "content_type": stat.content_type,
                "cached": False,
            }
        except Exception as e:
            logger.warning(f"[MediaCache] Failed to get file info: {object_key}, {e}")
            return None

    async def get_or_download(self, object_key: str) -> Path | None:
        """캐시에서 파일 가져오거나 S3에서 다운로드.

        Returns:
            캐시 파일 경로 또는 None (실패 시)
        """
        cache_path = self._get_cache_path(object_key)

        # 캐시 히트 확인
        if cache_path.exists():
            stat = cache_path.stat()
            if time.time() - stat.st_mtime < CACHE_TTL_SECONDS:
                logger.debug(f"[MediaCache] Cache hit: {object_key}")
                # 접근 시간 갱신 (LRU 용)
                cache_path.touch()
                return cache_path
            else:
                # 만료된 캐시 삭제
                await self._delete_cache_file(cache_path)

        # S3에서 다운로드
        logger.info(f"[MediaCache] Cache miss, downloading: {object_key}")
        client = get_s3_client()
        if not client:
            logger.error("[MediaCache] S3 client not available")
            return None

        try:
            # 임시 파일로 다운로드 후 이동 (atomic)
            temp_path = cache_path.with_suffix(".tmp")
            client.fget_object(
                self.settings.s3_bucket,
                object_key,
                str(temp_path),
            )
            temp_path.rename(cache_path)
            logger.info(f"[MediaCache] Downloaded and cached: {object_key}")

            # 캐시 정리 트리거 (비동기)
            asyncio.create_task(self._cleanup_if_needed())

            return cache_path

        except Exception as e:
            logger.error(f"[MediaCache] Download failed: {object_key}, {e}")
            # 임시 파일 정리
            temp_path = cache_path.with_suffix(".tmp")
            if temp_path.exists():
                temp_path.unlink()
            return None

    async def stream_file(
        self,
        object_key: str,
        start: int = 0,
        end: int | None = None,
    ) -> AsyncGenerator[bytes, None]:
        """파일을 청크 단위로 스트리밍.

        Args:
            object_key: S3 object key
            start: 시작 바이트 (Range 요청용)
            end: 끝 바이트 (Range 요청용, None이면 끝까지)

        Yields:
            파일 청크 (bytes)
        """
        cache_path = await self.get_or_download(object_key)
        if not cache_path:
            return

        async with aiofiles.open(cache_path, "rb") as f:
            await f.seek(start)

            remaining = (end - start + 1) if end else None

            while True:
                chunk_size = CHUNK_SIZE
                if remaining is not None:
                    chunk_size = min(chunk_size, remaining)

                chunk = await f.read(chunk_size)
                if not chunk:
                    break

                yield chunk

                if remaining is not None:
                    remaining -= len(chunk)
                    if remaining <= 0:
                        break

    async def _delete_cache_file(self, cache_path: Path) -> None:
        """캐시 파일 삭제."""
        try:
            if cache_path.exists():
                cache_path.unlink()
            meta_path = self._get_meta_path(cache_path)
            if meta_path.exists():
                meta_path.unlink()
        except Exception as e:
            logger.warning(f"[MediaCache] Failed to delete cache: {cache_path}, {e}")

    async def _cleanup_if_needed(self) -> None:
        """캐시 크기 제한 초과 시 정리."""
        if self._cleanup_lock.locked():
            return  # 이미 정리 중

        async with self._cleanup_lock:
            try:
                total_size = 0
                files: list[tuple[Path, float, int]] = []

                for path in self.cache_dir.iterdir():
                    if path.suffix == ".tmp" or path.suffix == ".meta":
                        continue
                    stat = path.stat()
                    total_size += stat.st_size
                    files.append((path, stat.st_mtime, stat.st_size))

                max_size = CACHE_MAX_SIZE_MB * 1024 * 1024

                if total_size <= max_size:
                    return

                # LRU: 오래된 파일부터 삭제
                files.sort(key=lambda x: x[1])  # mtime 기준 정렬

                for path, mtime, size in files:
                    if total_size <= max_size * 0.8:  # 80%까지 정리
                        break
                    await self._delete_cache_file(path)
                    total_size -= size
                    logger.info(f"[MediaCache] Evicted: {path.name}")

            except Exception as e:
                logger.error(f"[MediaCache] Cleanup failed: {e}")

    async def cleanup_expired(self) -> int:
        """만료된 캐시 파일 정리.

        Returns:
            삭제된 파일 수
        """
        deleted = 0
        now = time.time()

        try:
            for path in self.cache_dir.iterdir():
                if path.suffix == ".tmp" or path.suffix == ".meta":
                    continue

                stat = path.stat()
                if now - stat.st_mtime > CACHE_TTL_SECONDS:
                    await self._delete_cache_file(path)
                    deleted += 1

        except Exception as e:
            logger.error(f"[MediaCache] Expired cleanup failed: {e}")

        if deleted > 0:
            logger.info(f"[MediaCache] Cleaned up {deleted} expired files")

        return deleted


# 싱글톤 인스턴스
_media_cache_service: MediaCacheService | None = None


def get_media_cache_service() -> MediaCacheService:
    """MediaCacheService 싱글톤 반환."""
    global _media_cache_service
    if _media_cache_service is None:
        _media_cache_service = MediaCacheService()
    return _media_cache_service
