# vLLM 0.26 ROCm container for AMD Radeon 890M (gfx1150) on WSL2.
#
# Production image for serving Gemma 4 / Qwen 3.5 / etc on consumer Ryzen AI
# APUs through Docker. Bakes the three runtime fixes that make the AMD nightly
# vLLM image actually work in our environment:
#
#   1. librocdxg.so          — WSL ROCm bridge (extracted from rocm/librocdxg
#                                v1.1.2 deb, 2026-04-24 release with APU
#                                memory enhancements)
#   2. librocprofiler-sdk    — no-op stub providing the symbols PyTorch links
#                              against. The real lib FATAL-aborts in WSL
#                              because /sys/class/kfd doesn't exist.
#   3. amdsmi (Python)       — fake module so vLLM's rocm_platform_plugin()
#                              detects the GPU via torch.cuda instead of the
#                              real amdsmi (which needs /dev/kfd).
#
# At runtime mount /dev/dxg and the WSL libdxcore:
#   --device=/dev/dxg
#   -v /usr/lib/wsl/lib/libdxcore.so:/usr/lib/libdxcore.so
#   -e HSA_ENABLE_DXG_DETECTION=1

FROM vllm/vllm-openai-rocm:v0.26.0@sha256:5709fafe47123becb2f5e61c32d0b97beff1a629bb40bb753c15464f69a97a18

# (1) Real librocdxg.so from rocm/librocdxg v1.1.2 release deb.
COPY librocdxg.so /opt/rocm/lib/librocdxg.so

# (2) Stub librocprofiler-sdk.so to bypass PyTorch 2.11 + WSL FATAL.
#     Replaces every variant the dynamic linker may resolve.
COPY librocprofiler-sdk-stub.so /opt/rocm/lib/librocprofiler-sdk.so.1.1.0

# Register the WSL ROCm bridge before any Python build checks.
RUN ldconfig

# (3) Fake amdsmi Python module so vLLM platform detection succeeds.
COPY amdsmi_fake.py /usr/local/lib/python3.12/dist-packages/amdsmi/__init__.py

# Backport the Gemma 4 target-width MTP embedding fix from vLLM main.
COPY patch_gemma4_mtp.py /tmp/patch_gemma4_mtp.py
RUN python /tmp/patch_gemma4_mtp.py

# Required by vLLM's OpenAI-compatible audio transcription endpoint.
RUN pip install --no-cache-dir \
    'soundfile==0.14.0' \
    'av==18.0.0'

# Sanity check at build time — chat and audio runtimes must both import.
RUN python -c "import vllm; print('vllm', vllm.__version__)" \
 && python -c "import transformers; print('transformers', transformers.__version__)" \
 && python -c "import soundfile, av; print('audio decoders ok')"

# Initialize DXG before vLLM probes it in each Python subprocess. Copy this
# last so Docker build-time Python commands do not require a GPU.
COPY sitecustomize.py /usr/lib/python3.12/sitecustomize.py

# The upstream image entrypoint is `vllm serve`; compose supplies that command.
ENTRYPOINT []

WORKDIR /workspace
