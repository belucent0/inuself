#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Celery 워커 실행 스크립트 (PM2용)."""
import sys
import os
import logging
import io

# Windows에서 UTF-8 인코딩 설정 (traceback 출력 깨짐 방지)
if os.name == "nt":
    # Python의 기본 인코딩을 UTF-8로 설정
    if sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    if sys.stderr.encoding != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    # 환경 변수 설정
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # traceback 출력도 UTF-8로 처리
    import traceback
    # traceback 모듈의 기본 인코딩 설정 (Python 3.7+)
    if hasattr(traceback, "print_exc"):
        # traceback 출력을 UTF-8로 강제
        original_print_exc = traceback.print_exc
        
        def utf8_print_exc(*args, **kwargs):
            """UTF-8 인코딩으로 traceback 출력."""
            if "file" not in kwargs:
                kwargs["file"] = sys.stderr
            try:
                return original_print_exc(*args, **kwargs)
            except UnicodeEncodeError:
                # 인코딩 실패 시 ASCII로 대체
                kwargs["file"] = io.TextIOWrapper(sys.stderr.buffer, encoding="ascii", errors="replace")
                return original_print_exc(*args, **kwargs)
        
        traceback.print_exc = utf8_print_exc

# backend 디렉토리를 Python 경로에 추가
backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)

# 로깅 설정을 먼저 적용 (Celery가 초기화되기 전)
def setup_logging_format():
    """로깅 포맷을 일관되게 설정 (타임스탬프 제거)."""
    # 타임스탬프를 제거한 포맷 (Loguru와 일관성 유지)
    formatter = logging.Formatter(
        "%(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
    )
    
    # 루트 로거 포맷 설정
    root_logger = logging.getLogger()
    # 기존 핸들러 제거 후 새로 추가 (Celery가 추가한 핸들러를 덮어쓰기 위해)
    root_logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    
    # Celery 관련 로거도 미리 설정
    for logger_name in ["celery", "celery.task", "celery.worker", "celery.worker.strategy"]:
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False  # 루트 로거로 전파 방지

# Celery 워커 실행
if __name__ == "__main__":
    # 로깅 포맷 설정 (Celery 초기화 전에 적용)
    setup_logging_format()
    
    from celery import __main__
    
    # 순차/병렬 처리 설정 (기본값: 순차 처리)
    # SEQUENTIAL_PROCESSING=true (기본값) -> concurrency=1 (ASR과 LLM 순차 처리)
    # SEQUENTIAL_PROCESSING=false -> concurrency=2 (ASR과 LLM 동시 처리)
    sequential_processing = os.getenv("SEQUENTIAL_PROCESSING", "true").lower() == "true"
    concurrency = 1 if sequential_processing else 2
    
    print(f"[Celery Worker] Sequential Processing: {sequential_processing} (concurrency={concurrency})")
    
    # Celery 명령줄 인자 설정
    sys.argv = [
        "celery",
        "-A", "app.worker.celery_app",
        "worker",
        "--pool=solo",
        f"--concurrency={concurrency}",
        "--loglevel=info",
        "--max-tasks-per-child=100",
        "--task-events",
    ]
    
    __main__.main()

