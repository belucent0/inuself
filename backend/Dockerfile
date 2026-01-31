# ===========================================
# Stage 1: 빌드 (uv 사용)
# ===========================================
FROM python:3.12-slim AS builder

WORKDIR /app

# 빌드에 필요한 시스템 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# uv 설치 (공식 이미지에서 복사)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# uv 환경 변수 설정
ENV UV_SYSTEM_PYTHON=1
ENV UV_COMPILE_BYTECODE=1

# 의존성 파일 복사
COPY pyproject.toml uv.lock ./

# 의존성 설치 (uv pip 방식 - 시스템 Python에 직접 설치)
RUN uv pip install --system -r pyproject.toml

# ===========================================
# Stage 2: 런타임 (빌드 도구 제외)
# ===========================================
FROM python:3.12-slim

WORKDIR /app

# 런타임에 필요한 시스템 의존성만 설치
# - ffmpeg: 오디오/비디오 처리
# - curl, unzip: Deno 설치용
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno 설치 (yt-dlp YouTube 다운로드에 필요, 2025.11.12 이후)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

# 빌드 단계에서 설치된 Python 패키지 복사
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 애플리케이션 코드 복사
COPY . .

# 포트 노출
EXPOSE 8000

# 헬스체크
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Alembic 마이그레이션 실행 후 서버 시작
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
