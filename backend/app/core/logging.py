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


# safe_print를 loguru로 대체하는 함수
def safe_print(*args, **kwargs) -> None:
    """
    Windows cp949 인코딩 문제를 피하기 위한 안전한 print 함수.
    loguru를 사용하여 UTF-8로 출력합니다.
    """
    # kwargs에서 sep, end 등을 처리
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\n")
    
    message = sep.join(str(arg) for arg in args)
    if end != "\n":
        message = message.rstrip("\n") + end
    
    logger.info(message)


# 외부 라이브러리 로거 억제 설정
def configure_external_loggers() -> None:
    """외부 라이브러리의 로그 레벨 조정."""
    import logging
    
    # llama_cpp 로거 억제
    llama_logger = logging.getLogger("llama_cpp")
    llama_logger.setLevel(logging.ERROR)
    
    # pyannote 로거 억제 (선택적)
    pyannote_logger = logging.getLogger("pyannote")
    pyannote_logger.setLevel(logging.WARNING)
    
    # torch 로거 억제 (선택적)
    torch_logger = logging.getLogger("torch")
    torch_logger.setLevel(logging.WARNING)


# 모듈 import 시 자동으로 외부 로거 설정
configure_external_loggers()

