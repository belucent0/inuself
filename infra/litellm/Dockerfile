FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# LiteLLM 및 의존성 설치
# 충돌 방지를 위해 pip로 직접 설치
RUN pip install --no-cache-dir "litellm[proxy]" redis prometheus_client \
    opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http \
    opentelemetry-instrumentation-httpx opentelemetry-instrumentation-redis \
    opentelemetry-instrumentation-fastapi

# Custom handler 및 스크립트 복사
COPY . /app/custom/

# Config 파일 복사 (볼륨 마운트 백업)
COPY litellm_config.yaml /app/config.yaml

# 환경변수 설정
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1

CMD ["python", "/app/custom/run_proxy.py"]
