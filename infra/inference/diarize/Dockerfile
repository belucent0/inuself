# Speaker diarization (pyannote.audio) container — WSL ROCm gfx1150
#
# Base: AMD nightly vLLM image (has torch 2.10 ROCm + amdsmi + librocprofiler-sdk).
# Adds: ffmpeg system libs (for torchcodec audio decoding) + pyannote.audio.
#
# At runtime, mount the same WSL stubs as the vLLM container:
#   --device=/dev/dxg
#   -v /usr/lib/wsl/lib/libdxcore.so:/usr/lib/libdxcore.so
#   -v <project>/infra/inference/librocdxg/librocdxg.so:/opt/rocm/lib/librocdxg.so:ro
#   -v <project>/infra/inference/stubs/librocprofiler-sdk-stub.so:/opt/rocm/lib/librocprofiler-sdk.so.1.1.0:ro
#   -v <project>/infra/inference/stubs/amdsmi_fake.py:/usr/local/lib/python3.12/dist-packages/amdsmi/__init__.py:ro
#   -e HSA_ENABLE_DXG_DETECTION=1
FROM rocm/vllm-dev:rocm7.2.1_navi_ubuntu24.04_py3.12_pytorch_2.9_vllm_0.16.0

ENV DEBIAN_FRONTEND=noninteractive

# ffmpeg provides libavutil/libavcodec/libavformat that torchcodec links against.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Stable torch 2.9 base → use latest pyannote.audio (4.x). This is the same
# version (4.0.3) the host's rocm_env uses successfully.
#
# CRITICAL: pyannote.audio's PyPI metadata pulls vanilla CUDA torch 2.8 + nvidia-*
# wheels which would replace this image's ROCm-built torch 2.9.1+rocm. We use
# a constraints file to pin the existing ROCm torch/torchaudio/torchcodec
# versions so pip leaves them alone.
# pyannote.audio 4.0.3 hard-pins torch==2.8.0, our base has ROCm torch 2.9.1.
# Install pyannote.audio with --no-deps then add only non-torch dependencies.
# This is what the host's rocm_env effectively does.
RUN pip install --no-cache-dir --no-deps 'pyannote.audio==4.0.3' \
 && pip install --no-cache-dir \
        'pyannote.core>=6.0' \
        'pyannote.database>=6.1' \
        'pyannote.metrics>=4.0' \
        'pyannote.pipeline>=4.0' \
        'pyannoteai-sdk>=0.4' \
        'lightning>=2.6,<3' \
        'pytorch-lightning>=2.6,<3' \
        'pytorch-metric-learning>=2.9,<3' \
        'torchmetrics>=1.9,<2' \
        'torch-audiomentations>=0.12' \
        'torch-pitch-shift>=1.2' \
        'asteroid-filterbanks>=0.4' \
        'julius>=0.2' \
        'primePy>=1.3' \
        'optuna>=4' \
        'einops>=0.8' \
        'soundfile>=0.13' \
        'opentelemetry-api>=1.41' \
        'opentelemetry-sdk>=1.41' \
        'opentelemetry-exporter-otlp-proto-http>=1.41'

# FastAPI 서비스화 — ai-gateway가 직접 /diarize 호출 (Provider Manager 우회)
RUN pip install --no-cache-dir \
        'fastapi>=0.115' 'uvicorn[standard]>=0.32' 'python-multipart>=0.0.20'

COPY server.py /app/server.py

# Ensure dynamic linker sees freshly-installed libs.
RUN ldconfig

WORKDIR /app

EXPOSE 8003

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8003"]
