"""Loguru 로깅 설정 모듈."""
import sys
from pathlib import Path
from loguru import logger

# 기본 로거 제거
logger.remove()

# 로그 포맷 설정
log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# 콘솔 출력 설정
logger.add(
    sys.stderr,
    format=log_format,
    level="INFO",
    colorize=True,
    backtrace=True,
    diagnose=True,
)

# 파일 로깅 설정 (선택적)
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
    log_file = log_dir / "app.log"
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
    error_log_file = log_dir / "error.log"
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


# safe_print는 더 이상 사용하지 않습니다. logger를 직접 사용하세요.
# 예: logger.info("message") 또는 logger.error("error message")


# Python logging 포맷 설정 (타임스탬프 제거 - Loguru가 이미 타임스탬프를 추가함)
def configure_python_logging() -> None:
    """Python 표준 logging 모듈의 포맷을 일관되게 설정."""
    import logging
    
    # 루트 로거의 핸들러 포맷 수정
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        # 타임스탬프를 제거한 포맷 (Loguru와 일관성 유지)
        formatter = logging.Formatter(
            "%(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
        )
        handler.setFormatter(formatter)
    
    # Celery 로거 포맷 설정
    celery_logger = logging.getLogger("celery")
    for handler in celery_logger.handlers:
        formatter = logging.Formatter(
            "%(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
        )
        handler.setFormatter(formatter)
    
    # Celery task 로거 포맷 설정
    celery_task_logger = logging.getLogger("celery.task")
    for handler in celery_task_logger.handlers:
        formatter = logging.Formatter(
            "%(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
        )
        handler.setFormatter(formatter)


# 외부 라이브러리 로거 억제 설정
def configure_external_loggers() -> None:
    """외부 라이브러리의 로그 레벨 조정."""
    import logging
    
    # pyannote 로거 억제 (선택적)
    pyannote_logger = logging.getLogger("pyannote")
    pyannote_logger.setLevel(logging.WARNING)
    
    # torch 로거 억제 (선택적)
    torch_logger = logging.getLogger("torch")
    torch_logger.setLevel(logging.WARNING)


# 모듈 import 시 자동으로 외부 로거 설정
configure_external_loggers()
# Python logging 포맷 설정 (나중에 호출되어 Celery가 초기화된 후 적용)
# configure_python_logging()는 celery_app이 초기화된 후에 호출되어야 함

