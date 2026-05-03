# Qwen3-VL-8B-Instruct (Q4_K_M GGUF) on llama.cpp HIP — accuracy-tier OCR
#
# Tier-2 (accuracy) OCR for the asr-ocr service. Built on llama.cpp's HIP
# backend (-DGGML_HIP=ON, gfx1150) following the same pattern proven by
# infra/inference/vibevoicecpp/Dockerfile.
#
# - Model: unsloth/Qwen3-VL-8B-Instruct-GGUF (Q4_K_M ~5 GB)
# - Vision proj: mmproj-F16.gguf (~1 GB)
# - Server: llama-server with --mmproj for OpenAI-compatible /v1/chat/completions
#
# Why llama.cpp not vLLM: vLLM does not yet ship a Qwen3-VL kernel; the
# llama.cpp/ggml team merged Qwen3-VL multimodal support in early 2026.

FROM rocm/vllm-dev:rocm7.2.1_navi_ubuntu24.04_py3.12_pytorch_2.9_vllm_0.16.0

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        git build-essential cmake ninja-build \
        libcurl4-openssl-dev pkg-config ca-certificates \
 && rm -rf /var/lib/apt/lists/*

ENV ROCM_PATH=/opt/rocm
ENV PATH=/opt/rocm/bin:$PATH
ENV HIP_PLATFORM=amd

# WSL ROCm bridge — required for any ROCm runtime call inside WSL containers.
# Same two binaries baked into infra/inference/llm/Dockerfile (vLLM image).
COPY librocdxg.so /opt/rocm/lib/librocdxg.so
COPY librocprofiler-sdk-stub.so /opt/rocm/lib/librocprofiler-sdk.so.1.1.0

# Build llama.cpp with HIP backend targeting gfx1150 (Radeon 890M / Ryzen AI 9 HX 370).
# Same flag set as vibevoicecpp: hipcc as both C and C++ compiler, gfx1150 only.
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp.git /opt/llama.cpp \
 && cd /opt/llama.cpp \
 && cmake -B build -G Ninja \
        -DGGML_HIP=ON \
        -DAMDGPU_TARGETS=gfx1150 \
        -DGPU_TARGETS=gfx1150 \
        -DCMAKE_C_COMPILER=/opt/rocm/bin/hipcc \
        -DCMAKE_CXX_COMPILER=/opt/rocm/bin/hipcc \
        -DCMAKE_BUILD_TYPE=Release \
        -DLLAMA_CURL=ON \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=ON \
 && cmake --build build -j --target llama-server llama-cli 2>&1 | tail -50 \
 && cp build/bin/llama-server /usr/local/bin/llama-server \
 && cp build/bin/llama-cli /usr/local/bin/llama-cli \
 && ldconfig

# Auto-download model from HF cache + exec llama-server.
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

WORKDIR /opt/llama.cpp

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
