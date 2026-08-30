# vLLM 0.28 ROCm container for AMD Radeon 890M (gfx1150) on WSL2.
#
# Production image for serving Gemma 4 / Qwen 3.5 / etc on consumer Ryzen AI
# APUs through Docker. Bakes the two runtime fixes that make the upstream
# vLLM image work in our environment:
#
#   1. librocdxg.so          — WSL ROCm bridge (extracted from rocm/librocdxg
#                                v1.1.2 deb, 2026-04-24 release with APU
#                                memory enhancements)
#   2. amdsmi (Python)       — fake module so vLLM's rocm_platform_plugin()
#                              detects the GPU via torch.cuda instead of the
#                              real amdsmi (which needs /dev/kfd).
#
# Compose disables rocprofiler registration at runtime. PyTorch 2.12 needs
# symbols absent from the old no-op profiler stub used by vLLM 0.26.
#
# At runtime mount /dev/dxg and the WSL libdxcore:
#   --device=/dev/dxg
#   -v /usr/lib/wsl/lib/libdxcore.so:/usr/lib/libdxcore.so
#   -e HSA_ENABLE_DXG_DETECTION=1

FROM vllm/vllm-openai-rocm:v0.28.0@sha256:e0a3b2bd3fe7ec563916c3a5d949898d133458c18d6b2f460c906885cfb32032

# (1) Real librocdxg.so from rocm/librocdxg v1.1.2 release deb.
COPY librocdxg.so /opt/rocm/lib/librocdxg.so

# Register the WSL ROCm bridge before any Python build checks.
RUN ldconfig

# (2) Fake amdsmi Python module so vLLM platform detection succeeds.
COPY amdsmi_fake.py /usr/local/lib/python3.12/dist-packages/amdsmi/__init__.py

# Required by vLLM's OpenAI-compatible audio transcription endpoint.
RUN pip install --no-cache-dir \
    'soundfile==0.14.0' \
    'av==18.0.0'

# Sanity check at build time — chat and audio runtimes must both import.
RUN python -c "import vllm; assert vllm.__version__ == '0.28.0'; print('vllm', vllm.__version__)" \
 && python -c "import transformers; print('transformers', transformers.__version__)" \
 && python -c "import soundfile, av; print('audio decoders ok')"

# Initialize DXG before vLLM probes it in each Python subprocess. Copy this
# last so Docker build-time Python commands do not require a GPU.
COPY sitecustomize.py /usr/lib/python3.12/sitecustomize.py

# The upstream image entrypoint is `vllm serve`; compose supplies that command.
ENTRYPOINT []

WORKDIR /workspace
