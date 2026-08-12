# whisper.cpp HIP build (PoC for ASR backend benchmark)
#
# Builds whisper.cpp with HIP backend targeting gfx1150 (Radeon 890M / Strix
# Point). Same recipe as infra/inference/ocr-qwen/Dockerfile but swapping
# llama.cpp → whisper.cpp.
#
# Endpoints (whisper-server, OpenAI-incompatible native API):
#   POST /inference     multipart audio → JSON transcript
#   POST /load          change model
#   GET  /              status
#
# Bench/debug:
#   docker exec ai-asr-whispercpp whisper-cli -m /models/<file>.bin -f /audio/<wav>

FROM rocm/vllm-dev:rocm7.2.1_navi_ubuntu24.04_py3.12_pytorch_2.9_vllm_0.16.0

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        git build-essential cmake ninja-build \
        libcurl4-openssl-dev pkg-config ca-certificates \
        wget ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ENV ROCM_PATH=/opt/rocm
ENV PATH=/opt/rocm/bin:$PATH
ENV HIP_PLATFORM=amd

# WSL ROCm bridge — identical to ocr-qwen / llm. Required for /dev/dxg
# (DirectX-Graphics) ROCm runtime calls inside WSL containers.
COPY librocdxg.so /opt/rocm/lib/librocdxg.so
COPY librocprofiler-sdk-stub.so /opt/rocm/lib/librocprofiler-sdk.so.1.1.0

# Build whisper.cpp with HIP backend, gfx1150 only.
RUN git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git /opt/whisper.cpp \
 && cd /opt/whisper.cpp \
 && cmake -B build -G Ninja \
        -DGGML_HIP=ON \
        -DAMDGPU_TARGETS=gfx1150 \
        -DGPU_TARGETS=gfx1150 \
        -DCMAKE_C_COMPILER=/opt/rocm/bin/hipcc \
        -DCMAKE_CXX_COMPILER=/opt/rocm/bin/hipcc \
        -DCMAKE_BUILD_TYPE=Release \
        -DWHISPER_BUILD_TESTS=OFF \
        -DWHISPER_BUILD_EXAMPLES=ON \
 && cmake --build build -j --target whisper-cli whisper-server 2>&1 | tail -50 \
 && cp build/bin/whisper-cli /usr/local/bin/whisper-cli \
 && cp build/bin/whisper-server /usr/local/bin/whisper-server \
 && ldconfig

# 모델은 entrypoint.sh가 named volume(`whisper-models`)에 first-boot 시 다운로드.
# 이미지 layer에 굽지 않는 이유: 1.6GB라 Dockerfile 변경마다 재다운로드 발생.
ENV WHISPER_MODEL_FILE=ggml-large-v3-turbo.bin
ENV WHISPER_MODEL_URL=https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
RUN mkdir -p /models

# FastAPI adapter: /transcribe (기존 ai-asr 인터페이스 호환) → whisper-server /inference 변환.
RUN pip install --no-cache-dir \
        fastapi 'uvicorn[standard]' python-multipart httpx soundfile

COPY adapter.py /app/adapter.py
COPY entrypoint.sh /usr/local/bin/asr-entrypoint.sh
RUN chmod +x /usr/local/bin/asr-entrypoint.sh

WORKDIR /app

# Adapter (외부)=:8001 /transcribe ; whisper-server (내부 only)=:8002 /inference
EXPOSE 8001

CMD ["/usr/local/bin/asr-entrypoint.sh"]
