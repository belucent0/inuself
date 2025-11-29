#!/usr/bin/env python
"""Celery 워커 실행 스크립트 (PM2용)."""
import sys
import os

# backend 디렉토리를 Python 경로에 추가
backend_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_dir)

# Celery 워커 실행
if __name__ == "__main__":
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

