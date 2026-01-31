FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 설치 (최소화)
# - ffmpeg: 오디오 변환 (WAV), duration 추출
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv 설치 (공식 이미지에서 복사)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# uv 환경 변수 설정
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1

# 의존성 설치
COPY worker/pyproject.toml worker/uv.lock* ./
RUN uv pip install --system -r pyproject.toml && \
    rm -rf /root/.cache && \
    find /usr/local/lib/python3.12 -name "*.pyc" -delete && \
    find /usr/local/lib/python3.12 -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 소스 코드 복사
COPY . .

# Celery Worker 실행
ENV PYTHONPATH=/app

CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info", "-Q", "asr_tasks,llm_summary,ocr_tasks"]
