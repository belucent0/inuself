FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 설치 (최소화)
# - ffmpeg: 오디오 변환 (WAV), duration 추출
# - libreoffice: Office 문서 → PDF 변환 (OCR 전처리) - 필요 시 주석 해제
# --no-install-recommends로 불필요한 의존성 제외
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# LibreOffice 설치 (Office 문서 OCR 필요 시 주석 해제)
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     libreoffice-writer \
#     libreoffice-calc \
#     libreoffice-impress \
#     && apt-get clean \
#     && rm -rf /var/lib/apt/lists/* \
#     && rm -rf /tmp/* /var/tmp/* \
#     && rm -rf /usr/share/doc /usr/share/man \
#     && rm -rf /usr/share/icons /usr/share/pixmaps

# Poetry 설치
RUN pip install --no-cache-dir poetry

# Poetry 설정 (가상환경 생성 X)
RUN poetry config virtualenvs.create false && \
    poetry config installer.max-workers 10

# 네트워크 타임아웃 환경변수 설정 (300초)
ENV POETRY_REQUESTS_TIMEOUT=300

# 의존성 설치
COPY worker/pyproject.toml worker/poetry.lock* ./
# poetry.lock이 pyproject.toml과 불일치할 수 있으므로 자동 갱신
RUN poetry lock --no-update || poetry lock
RUN poetry install --no-interaction --no-ansi --without dev && \
    rm -rf /root/.cache && \
    find /usr/local/lib/python3.12 -name "*.pyc" -delete && \
    find /usr/local/lib/python3.12 -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 소스 코드 복사
COPY . .

# Celery Worker 실행
ENV PYTHONPATH=/app

CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info", "-Q", "asr_tasks,llm_summary,ocr_tasks"]
