"""YouTube 다운로드 서비스

YouTube URL 검증 및 영상 다운로드 기능을 제공합니다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yt_dlp

from ..core.logging import logger

# curl_cffi 설치 여부 확인 (impersonate 기능에 필요)
try:
    import curl_cffi
    from yt_dlp.networking.impersonate import ImpersonateTarget
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    ImpersonateTarget = None
    logger.warning("[YouTube] curl_cffi not available - impersonate feature disabled")

# 최대 영상 길이 (초) - 1시간
MAX_DURATION_SECONDS = 3600


class InvalidYouTubeURLError(Exception):
    """유효하지 않은 YouTube URL"""
    pass


class YouTubeDownloadError(Exception):
    """YouTube 다운로드 실패"""
    pass


class VideoDurationExceededError(Exception):
    """영상 길이 초과"""
    pass


@dataclass
class YouTubeVideoInfo:
    """YouTube 영상 정보"""
    video_id: str
    title: str
    duration: int  # seconds
    temp_path: Path | None = None


class YouTubeService:
    """YouTube 영상 검증 및 다운로드 서비스"""

    # YouTube URL 정규식 패턴
    YOUTUBE_PATTERNS = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:m\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
    ]

    def validate_youtube_url(self, url: str) -> str:
        """
        YouTube URL 검증 및 video_id 추출
        
        Args:
            url: YouTube URL
            
        Returns:
            str: video_id (11자)
            
        Raises:
            InvalidYouTubeURLError: 유효하지 않은 URL인 경우
        """
        for pattern in self.YOUTUBE_PATTERNS:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        raise InvalidYouTubeURLError(f"Invalid YouTube URL: {url}")

    def _get_base_opts(self) -> dict:
        """
        yt-dlp 기본 옵션 반환 (403/Empty File 에러 우회)

        2025년 이후 YouTube 다운로드에 필요한 옵션:
        - JavaScript 런타임 (Deno/Node.js)
        - 브라우저 impersonate (curl_cffi)
        - IPv4 강제
        - User-Agent 설정
        """
        opts = {
            # IPv4 강제 (일부 403 에러 우회)
            'source_address': '0.0.0.0',
            # User-Agent 설정
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            },
            # 재시도 설정
            'retries': 5,
            'fragment_retries': 5,
        }

        # 브라우저 impersonate (curl_cffi 필요)
        if CURL_CFFI_AVAILABLE and ImpersonateTarget:
            opts['impersonate'] = ImpersonateTarget('chrome', '131', 'macos', '14')
            logger.debug("[YouTube] Browser impersonate enabled (chrome-131:macos-14)")

        return opts

    def get_video_info(self, url: str) -> dict:
        """
        영상 정보 조회 (다운로드 없이)

        Args:
            url: YouTube URL

        Returns:
            dict: 영상 정보 (title, duration, video_id)

        Raises:
            YouTubeDownloadError: 정보 조회 실패
        """
        ydl_opts = {
            **self._get_base_opts(),
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
                return {
                    'title': info.get('title', 'Unknown'),
                    'duration': info.get('duration', 0),
                    'video_id': info.get('id'),
                }
            except yt_dlp.utils.DownloadError as e:
                logger.error(f"[YouTube] Failed to get video info: {e}")
                raise YouTubeDownloadError(f"Failed to get video info: {e}")
            except Exception as e:
                logger.error(f"[YouTube] Unexpected error getting video info: {e}")
                raise YouTubeDownloadError(f"Failed to get video info: {e}")

    def download_video(self, url: str, output_dir: Path) -> YouTubeVideoInfo:
        """
        YouTube 영상 다운로드 (360p 우선, 240p 폴백)
        
        Args:
            url: YouTube URL
            output_dir: 다운로드 파일 저장 디렉토리
            
        Returns:
            YouTubeVideoInfo: 다운로드된 파일 정보
            
        Raises:
            VideoDurationExceededError: 1시간 초과 영상
            YouTubeDownloadError: 다운로드 실패
        """
        # 먼저 영상 정보 조회하여 길이 확인
        info = self.get_video_info(url)
        duration = info.get('duration', 0)

        if duration > MAX_DURATION_SECONDS:
            raise VideoDurationExceededError(
                f"Video duration ({duration}s) exceeds maximum ({MAX_DURATION_SECONDS}s)"
            )

        video_id = info.get('video_id', 'unknown')
        title = info.get('title', 'Unknown')

        # 안전한 파일명 생성 (특수문자 제거)
        safe_title = re.sub(r'[^\w\s가-힣-]', '', title)[:50].strip()
        if not safe_title:
            safe_title = video_id
        output_template = str(output_dir / f"{video_id}_{safe_title}.%(ext)s")

        logger.info(f"[YouTube] Downloading: {title} ({duration}s)")
        logger.info(f"[YouTube] Output template: {output_template}")

        # 영상+오디오 다운로드 (best format)
        ydl_opts = {
            **self._get_base_opts(),
            'format': 'best[ext=mp4]/best',  # mp4 우선, 없으면 best
            'outtmpl': output_template,
            'quiet': False,
            'no_warnings': False,
            'merge_output_format': 'mp4',  # 합칠 때 mp4로
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Find downloaded file
            for file in output_dir.iterdir():
                if file.stem.startswith(video_id) and file.suffix in ('.mp4', '.webm', '.mkv', '.m4a', '.mp3', '.opus'):
                    logger.info(f"[YouTube] Download complete: {file}")
                    return YouTubeVideoInfo(
                        video_id=video_id,
                        title=title,
                        duration=duration,
                        temp_path=file,
                    )

            raise YouTubeDownloadError("Downloaded file not found")

        except YouTubeDownloadError:
            raise
        except yt_dlp.utils.DownloadError as e:
            logger.error(f"[YouTube] Download failed: {e}")
            raise YouTubeDownloadError(f"Download failed: {e}")
        except Exception as e:
            logger.error(f"[YouTube] Unexpected error during download: {e}")
            raise YouTubeDownloadError(f"Download failed: {e}")
