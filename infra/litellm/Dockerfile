FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# LiteLLM 및 의존성 설치
# 충돌 방지를 위해 pip로 직접 설치
RUN pip install --no-cache-dir "litellm[proxy]" redis prometheus_client

# 실행 스크립트 복사 (추후 Volume Mount로 덮어씌워질 수 있음)
COPY run_proxy.py /app/custom/run_proxy.py

# 환경변수 설정
ENV PYTHONUNBUFFERED=1
ENV PYTHONUTF8=1

CMD ["python", "/app/custom/run_proxy.py"]
