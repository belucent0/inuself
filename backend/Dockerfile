FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 설치
RUN apt-get update && apt-get install -y \
    curl \
    gcc \
    g++ \
    libsndfile1 \
    ffmpeg \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Poetry 설치
RUN pip install --no-cache-dir poetry==1.7.1

# Poetry 설정 (가상환경을 만들지 않음)
RUN poetry config virtualenvs.create false

# 의존성 파일 복사 및 설치 (API 서버용 - GPU 관련 제외)
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-interaction --no-ansi --no-root --without dev

# 애플리케이션 코드 복사
COPY . .

# 포트 노출
EXPOSE 8000

# 헬스체크를 위한 curl 확인
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Alembic 마이그레이션 실행 후 서버 시작
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

