FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 설치 (ffmpeg 필수)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Poetry 설치
RUN pip install --no-cache-dir poetry

# Poetry 설정 (가상환경 생성 X)
RUN poetry config virtualenvs.create false

# 의존성 설치
COPY worker/pyproject.toml worker/poetry.lock ./
RUN poetry install --no-interaction --no-ansi --without dev

# 소스 코드 복사 (Host의 worker 디렉토리 전체가 Context로 들어옴)
COPY . .

# Celery Worker 실행
# PYTHONPATH를 /app으로 설정하여 worker 패키지 인식
ENV PYTHONPATH=/app

CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info", "-Q", "asr_tasks,llm_summary,ocr_tasks"]
