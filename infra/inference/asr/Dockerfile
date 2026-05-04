# insanely-fast-whisper ROCm container — WSL gfx1150
#
# Base: stable navi vLLM image (torch 2.9 ROCm) — same as diarize.
# Adds: ffmpeg + insanely-fast-whisper. Audio is decoded with soundfile to
# avoid torchcodec ABI issues (same pattern as diarize).
#
# Runtime mount stack:
#   --device=/dev/dxg
#   -v /usr/lib/wsl/lib/libdxcore.so:/usr/lib/libdxcore.so
#   -v <project>/infra/inference/librocdxg/librocdxg.so:/opt/rocm/lib/librocdxg.so:ro
#   -e HSA_ENABLE_DXG_DETECTION=1
FROM rocm/vllm-dev:rocm7.2.1_navi_ubuntu24.04_py3.12_pytorch_2.9_vllm_0.16.0

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# insanely-fast-whisper bundles a CLI + transformers pipeline. We install with
# --no-deps and then add only its non-torch deps so the ROCm-built torch in
# the base image isn't replaced.
RUN pip install --no-cache-dir --no-deps insanely-fast-whisper \
 && pip install --no-cache-dir \
        'accelerate>=1.0' \
        'optimum>=1.20' \
        'rich>=13' \
        'pyannote.audio==4.0.3' --no-deps \
 && pip install --no-cache-dir \
        'pyannote.core>=6.0' 'pyannote.database>=6.1' \
        'pyannote.metrics>=4.0' 'pyannote.pipeline>=4.0' \
        'pyannoteai-sdk>=0.4' \
        'lightning>=2.6,<3' 'pytorch-lightning>=2.6,<3' \
        'pytorch-metric-learning>=2.9,<3' 'torchmetrics>=1.9,<2' \
        'torch-audiomentations>=0.12' 'torch-pitch-shift>=1.2' \
        'asteroid-filterbanks>=0.4' 'julius>=0.2' 'primePy>=1.3' 'optuna>=4' \
        'einops>=0.8' 'soundfile>=0.13' \
        'opentelemetry-api>=1.41' 'opentelemetry-sdk>=1.41' \
        'opentelemetry-exporter-otlp-proto-http>=1.41'

# FastAPI 서비스화 — ai-gateway가 직접 /transcribe 호출 (Provider Manager 우회)
RUN pip install --no-cache-dir \
        'fastapi>=0.115' 'uvicorn[standard]>=0.32' 'python-multipart>=0.0.20'

COPY server.py /app/server.py

RUN ldconfig

WORKDIR /app

EXPOSE 8001

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
