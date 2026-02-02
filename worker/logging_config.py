"""워커 로깅 설정 모듈."""
import sys
import logging
from pathlib import Path
from loguru import logger

# 기본 로거 제거
logger.remove()


def get_trace_id_safe() -> str:
    """OpenTelemetry trace_id를 안전하게 가져옴. 없으면 0 반환 (백그라운드 작업)."""
    try:
        from worker.telemetry import get_trace_id
        trace_id = get_trace_id()
        if trace_id and trace_id != "00000000000000000000000000000000":
            return trace_id
    except Exception:
        pass
    return "0"


def trace_id_patcher(record):
    """모든 로그 레코드에 trace_id를 자동으로 추가."""
    record["extra"]["trace_id"] = get_trace_id_safe()


# 로그 포맷 설정 (trace_id 포함)
log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "trace_id={extra[trace_id]} | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# trace_id patcher 적용
logger = logger.patch(trace_id_patcher)

# 콘솔 출력 설정
logger.add(
    sys.stderr,
    format=log_format,
    level="INFO",
    colorize=True,
    backtrace=True,
    diagnose=True,
)


def setup_file_logging(log_dir: Path | None = None, log_level: str = "INFO") -> None:
    """
    파일 로깅 설정.
    
    Args:
        log_dir: 로그 파일을 저장할 디렉토리 (None이면 파일 로깅 비활성화)
        log_level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    if log_dir is None:
        return
    
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # 일반 로그 파일
    log_file = log_dir / "worker.log"
    logger.add(
        log_file,
        format=log_format,
        level=log_level,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        backtrace=True,
        diagnose=True,
        encoding="utf-8",
    )
    
    # 에러 로그 파일
    error_log_file = log_dir / "worker_error.log"
    logger.add(
        error_log_file,
        format=log_format,
        level="ERROR",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        backtrace=True,
        diagnose=True,
        encoding="utf-8",
    )


def configure_external_loggers() -> None:
    """외부 라이브러리의 로그 레벨 조정."""
    # pyannote 로거 억제
    pyannote_logger = logging.getLogger("pyannote")
    pyannote_logger.setLevel(logging.WARNING)
    
    # torch 로거 억제
    torch_logger = logging.getLogger("torch")
    torch_logger.setLevel(logging.WARNING)
    
    # urllib3 로거 억제
    urllib3_logger = logging.getLogger("urllib3")
    urllib3_logger.setLevel(logging.WARNING)


# Celery 로깅 포맷 설정
_log_formatter = logging.Formatter(
    "%(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
)


def apply_celery_logging_format(celery_logger: logging.Logger) -> None:
    """Celery 로거에 포맷 적용."""
    for handler in celery_logger.handlers:
        handler.setFormatter(_log_formatter)


# 모듈 import 시 자동으로 외부 로거 설정
configure_external_loggers()
