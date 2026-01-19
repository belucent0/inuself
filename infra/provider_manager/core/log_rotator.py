"""Rotating Log Writer for Process Output.

프로세스 stdout/stderr를 파일로 리다이렉션할 때 사용하는
크기 기반 로그 로테이션 래퍼.

특징:
- 최대 파일 크기 제한 (기본 50MB)
- 백업 파일 개수 제한 (기본 3개)
- 스팸 라인 필터링 지원
- Thread-safe write
- subprocess PIPE를 통한 출력 처리 (ProcessOutputHandler)
"""

import os
import threading
import subprocess
from pathlib import Path
from typing import Optional, Set, Callable
import logging

logger = logging.getLogger("LogRotator")


class RotatingLogWriter:
    """크기 기반 로그 로테이션을 지원하는 파일 래퍼.

    subprocess.Popen의 stdout/stderr 인자로 사용할 수 있습니다.

    Usage:
        writer = RotatingLogWriter(
            Path("logs/server.log"),
            max_bytes=50*1024*1024,  # 50MB
            backup_count=3,
            spam_filters={"Enter 'exit'", "[FLM]  Enter"}
        )

        proc = subprocess.Popen(
            cmd,
            stdout=writer,
            stderr=subprocess.STDOUT,
        )

        # 종료 시
        writer.close()
    """

    def __init__(
        self,
        log_path: Path,
        max_bytes: int = 50 * 1024 * 1024,  # 50MB
        backup_count: int = 3,
        spam_filters: Optional[Set[str]] = None,
        encoding: str = "utf-8",
    ):
        """
        Args:
            log_path: 로그 파일 경로
            max_bytes: 최대 파일 크기 (바이트)
            backup_count: 유지할 백업 파일 개수
            spam_filters: 필터링할 문자열 집합 (이 문자열이 포함된 라인은 무시)
            encoding: 파일 인코딩
        """
        self.log_path = Path(log_path)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.spam_filters = spam_filters or set()
        self.encoding = encoding

        self._lock = threading.Lock()
        self._file: Optional[object] = None
        self._current_size = 0
        self._filtered_count = 0

        # 기존 파일 크기 확인
        if self.log_path.exists():
            self._current_size = self.log_path.stat().st_size

        # 파일 열기
        self._open_file()

    def _open_file(self) -> None:
        """로그 파일을 엽니다."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.log_path, "ab")  # append binary mode

    def _rotate(self) -> None:
        """로그 파일을 로테이션합니다."""
        if self._file:
            self._file.close()

        # 기존 백업 파일들 이동 (server.log.3 삭제, .2 -> .3, .1 -> .2, 현재 -> .1)
        for i in range(self.backup_count, 0, -1):
            src = self.log_path.with_suffix(f".log.{i}" if i > 0 else ".log")
            if i == self.backup_count:
                # 가장 오래된 백업 삭제
                backup_path = Path(str(self.log_path) + f".{i}")
                if backup_path.exists():
                    try:
                        backup_path.unlink()
                    except Exception as e:
                        logger.warning(f"Failed to delete old backup {backup_path}: {e}")

            # 이전 번호 백업을 다음 번호로 이동
            if i > 1:
                prev_backup = Path(str(self.log_path) + f".{i-1}")
                curr_backup = Path(str(self.log_path) + f".{i}")
                if prev_backup.exists():
                    try:
                        prev_backup.rename(curr_backup)
                    except Exception as e:
                        logger.warning(f"Failed to rotate {prev_backup} -> {curr_backup}: {e}")

        # 현재 파일을 .1로 이동
        backup_1 = Path(str(self.log_path) + ".1")
        if self.log_path.exists():
            try:
                self.log_path.rename(backup_1)
            except Exception as e:
                logger.warning(f"Failed to rotate current log to {backup_1}: {e}")

        # 새 파일 열기
        self._current_size = 0
        self._open_file()

        logger.info(f"Log rotated: {self.log_path} (filtered {self._filtered_count} spam lines)")
        self._filtered_count = 0

    def write(self, data: bytes) -> int:
        """데이터를 파일에 씁니다.

        Args:
            data: 쓸 데이터 (bytes)

        Returns:
            쓴 바이트 수
        """
        if not data:
            return 0

        with self._lock:
            # 스팸 필터링 (텍스트로 변환하여 체크)
            if self.spam_filters:
                try:
                    text = data.decode(self.encoding, errors="ignore")
                    for spam in self.spam_filters:
                        if spam in text:
                            self._filtered_count += 1
                            return len(data)  # 쓴 것처럼 반환 (subprocess가 멈추지 않도록)
                except Exception:
                    pass

            # 로테이션 필요 여부 확인
            if self._current_size + len(data) > self.max_bytes:
                self._rotate()

            # 데이터 쓰기
            if self._file:
                written = self._file.write(data)
                self._file.flush()
                self._current_size += written
                return written

            return 0

    def fileno(self) -> int:
        """파일 디스크립터 반환 (subprocess에서 필요)."""
        if self._file:
            return self._file.fileno()
        raise ValueError("Log file not open")

    def flush(self) -> None:
        """버퍼 플러시."""
        with self._lock:
            if self._file:
                self._file.flush()

    def close(self) -> None:
        """파일 닫기."""
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None

        if self._filtered_count > 0:
            logger.info(f"Log closed: {self.log_path} (filtered {self._filtered_count} spam lines total)")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# FLM 서버의 스팸 출력 필터
FLM_SPAM_FILTERS = {
    "Enter 'exit' to stop the server",
    "[FLM]  Enter 'exit'",
}


class ProcessOutputHandler:
    """프로세스 출력을 백그라운드 스레드에서 처리하는 핸들러.

    subprocess.PIPE로 출력을 받아 로테이팅 로그 파일에 씁니다.
    스팸 필터링과 로그 로테이션이 적용됩니다.

    Usage:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        handler = ProcessOutputHandler(
            proc,
            log_path=Path("logs/server.log"),
            spam_filters={"Enter 'exit'"},
        )
        handler.start()

        # 프로세스 종료 시
        handler.stop()
    """

    def __init__(
        self,
        process: subprocess.Popen,
        log_path: Path,
        max_bytes: int = 50 * 1024 * 1024,  # 50MB
        backup_count: int = 3,
        spam_filters: Optional[Set[str]] = None,
        encoding: str = "utf-8",
    ):
        """
        Args:
            process: subprocess.Popen 인스턴스 (stdout=PIPE 필수)
            log_path: 로그 파일 경로
            max_bytes: 최대 파일 크기 (바이트)
            backup_count: 유지할 백업 파일 개수
            spam_filters: 필터링할 문자열 집합
            encoding: 파일 인코딩
        """
        self.process = process
        self.log_writer = RotatingLogWriter(
            log_path=log_path,
            max_bytes=max_bytes,
            backup_count=backup_count,
            spam_filters=spam_filters,
            encoding=encoding,
        )

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _reader_thread(self) -> None:
        """프로세스 출력을 읽어 로그 파일에 쓰는 스레드."""
        try:
            while not self._stop_event.is_set():
                if self.process.stdout is None:
                    break

                # 한 줄씩 읽기 (블록킹)
                line = self.process.stdout.readline()
                if not line:
                    # EOF - 프로세스 종료
                    break

                # RotatingLogWriter에 쓰기 (필터링 + 로테이션)
                self.log_writer.write(line)

        except Exception as e:
            logger.error(f"Error in output handler thread: {e}")
        finally:
            self.log_writer.close()

    def start(self) -> None:
        """출력 처리 스레드 시작."""
        if self._thread is not None:
            return

        self._thread = threading.Thread(
            target=self._reader_thread,
            name=f"OutputHandler-{self.log_writer.log_path.stem}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """출력 처리 중지."""
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def is_alive(self) -> bool:
        """스레드가 실행 중인지 확인."""
        return self._thread is not None and self._thread.is_alive()


def create_provider_log_writer(
    provider_name: str,
    log_dir: Path,
    max_mb: int = 50,
    backup_count: int = 3,
) -> RotatingLogWriter:
    """프로바이더용 로그 라이터 생성.

    FLM 프로바이더의 경우 스팸 필터링이 자동으로 적용됩니다.

    Args:
        provider_name: 프로바이더 이름
        log_dir: 로그 디렉토리
        max_mb: 최대 파일 크기 (MB)
        backup_count: 백업 파일 개수

    Returns:
        RotatingLogWriter 인스턴스
    """
    log_path = log_dir / f"{provider_name}.log"

    # FLM 프로바이더는 스팸 필터 적용
    spam_filters = FLM_SPAM_FILTERS if provider_name.startswith("flm-") else None

    return RotatingLogWriter(
        log_path=log_path,
        max_bytes=max_mb * 1024 * 1024,
        backup_count=backup_count,
        spam_filters=spam_filters,
    )


def create_process_output_handler(
    process: subprocess.Popen,
    provider_name: str,
    log_dir: Path,
    max_mb: int = 50,
    backup_count: int = 3,
) -> ProcessOutputHandler:
    """프로바이더용 프로세스 출력 핸들러 생성.

    FLM 프로바이더의 경우 스팸 필터링이 자동으로 적용됩니다.

    Args:
        process: subprocess.Popen 인스턴스
        provider_name: 프로바이더 이름
        log_dir: 로그 디렉토리
        max_mb: 최대 파일 크기 (MB)
        backup_count: 백업 파일 개수

    Returns:
        ProcessOutputHandler 인스턴스 (start() 호출 필요)
    """
    log_path = log_dir / f"{provider_name}.log"

    # FLM 프로바이더는 스팸 필터 적용
    spam_filters = FLM_SPAM_FILTERS if provider_name.startswith("flm-") else None

    return ProcessOutputHandler(
        process=process,
        log_path=log_path,
        max_bytes=max_mb * 1024 * 1024,
        backup_count=backup_count,
        spam_filters=spam_filters,
    )
