# vLLM 0.20+ ROCm container for AMD Radeon 890M (gfx1150) on WSL2.
#
# Production image for serving Gemma 4 / Qwen 3.5 / etc on consumer Ryzen AI
# APUs through Docker. Bakes the three runtime fixes that make the AMD nightly
# vLLM image actually work in our environment:
#
#   1. librocdxg.so          — WSL ROCm bridge (extracted from rocm/librocdxg
#                                v1.1.2 deb, 2026-04-24 release with APU
#                                memory enhancements)
#   2. librocprofiler-sdk    — no-op stub providing the 19 symbols torch 2.10
#                              links against. The real lib FATAL-aborts in WSL
#                              because /sys/class/kfd doesn't exist.
#   3. amdsmi (Python)       — fake module so vLLM's rocm_platform_plugin()
#                              detects the GPU via torch.cuda instead of the
#                              real amdsmi (which needs /dev/kfd).
#
# At runtime mount /dev/dxg and the WSL libdxcore:
#   --device=/dev/dxg
#   -v /usr/lib/wsl/lib/libdxcore.so:/usr/lib/libdxcore.so
#   -e HSA_ENABLE_DXG_DETECTION=1

FROM rocm/vllm-dev:nightly_main_20260501

# (1) Real librocdxg.so from rocm/librocdxg v1.1.2 release deb.
COPY librocdxg.so /opt/rocm/lib/librocdxg.so

# (2) Stub librocprofiler-sdk.so to bypass torch 2.10 + WSL FATAL.
#     Replaces every variant the dynamic linker may resolve.
COPY librocprofiler-sdk-stub.so /opt/rocm/lib/librocprofiler-sdk.so.1.1.0

# (3) Fake amdsmi Python module so vLLM platform detection succeeds.
COPY amdsmi_fake.py /usr/local/lib/python3.12/dist-packages/amdsmi/__init__.py

# Sanity check at build time — image must still load torch.
RUN python -c "import vllm; print('vllm', vllm.__version__)" \
 && python -c "import transformers; print('transformers', transformers.__version__)"

# Ensure dynamic linker sees the new librocdxg.
RUN ldconfig

WORKDIR /workspace
