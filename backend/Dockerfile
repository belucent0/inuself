FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 설치
RUN apt-get update && apt-get install -y \
    curl \
    unzip \
    gcc \
    g++ \
    libsndfile1 \
    ffmpeg \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Deno 설치 (yt-dlp YouTube 다운로드에 필요, 2025.11.12 이후)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

# Poetry 설치
RUN pip install --no-cache-dir poetry==1.7.1

# Poetry 설정 (가상환경을 만들지 않음)
RUN poetry config virtualenvs.create false

# 의존성 파일 복사 및 설치 (API 서버용 - 워커 전용 패키지 제외)
# API 서버는 기본 의존성만 설치 (torch, pyannote.audio, docling 제외)
# 워커별 설치 방법:
#   - ASR 워커: poetry install --extras asr_worker
#   - LLM 워커: poetry install --extras llm_worker (추가 패키지 불필요, llama-server는 서브프로세스로 관리)
#   - OCR 워커: poetry install --extras ocr_worker
#   - 전체 워커: poetry install --extras gpu_worker
COPY pyproject.toml poetry.lock* ./
# poetry.lock이 pyproject.toml과 불일치할 수 있으므로 자동 갱신
# --no-update 실패 시 전체 lock 실행 (새 패키지 버전 반영)
RUN poetry lock --no-update || poetry lock
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

