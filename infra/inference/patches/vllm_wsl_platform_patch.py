"""Patch vLLM platform detection to bypass amdsmi (WSL-safe).

Replace the body of rocm_platform_plugin() with a torch.version.hip check,
since amdsmi cannot initialize inside WSL containers (no /dev/kfd).
"""
import sys
import vllm
import os

PATH = os.path.join(os.path.dirname(vllm.__file__), 'platforms', '__init__.py')

NEW_FN = '''def rocm_platform_plugin() -> str | None:
    is_rocm = False
    logger.debug("Checking if ROCm platform is available (torch-based, WSL-safe).")
    try:
        import torch
        if torch.cuda.is_available() and getattr(torch.version, "hip", None):
            is_rocm = True
            logger.debug("Confirmed ROCm via torch.version.hip=%s", torch.version.hip)
    except Exception as e:
        logger.debug("ROCm platform is not available because: %s", str(e))
    return "vllm.platforms.rocm.RocmPlatform" if is_rocm else None

'''

src = open(PATH).read()
start = src.index('def rocm_platform_plugin()')
nxt = src.index('\ndef ', start + 5)
old = src[start:nxt]

if 'WSL-safe' in old:
    print('[patch] already applied')
    sys.exit(0)

src2 = src.replace(old, NEW_FN, 1)
open(PATH, 'w').write(src2)
print('[patch] applied successfully')
