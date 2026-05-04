"""Prove pyannote speaker-diarization-3.1 actually runs on GPU (not CPU)
in this container. Three independent evidence lines:

  1. Inspect every nn.Module inside the pipeline and report its parameters'
     device (cuda vs cpu).
  2. Snapshot torch.cuda.memory_allocated() before/after model.to(cuda) and
     before/after a forward pass.
  3. Run identical inference twice — once on cuda, once on cpu — and compare
     wall time. CPU should be substantially slower.
"""
from __future__ import annotations

import os
import sys
import time

import soundfile as sf
import torch


def _find_token() -> str | None:
    tok = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
    if tok:
        return tok
    for p in ("/root/.cache/huggingface/token", os.path.expanduser("~/.cache/huggingface/token")):
        try:
            return open(p).read().strip()
        except FileNotFoundError:
            pass
    return None


def _device_summary(pipe: object) -> dict[str, int]:
    """Walk the pipeline and count parameters per device."""
    counts: dict[str, int] = {}
    for attr_name in dir(pipe):
        try:
            obj = getattr(pipe, attr_name)
        except Exception:
            continue
        if not isinstance(obj, torch.nn.Module):
            continue
        for p in obj.parameters():
            d = str(p.device)
            counts[d] = counts.get(d, 0) + p.numel()
    return counts


def _load_audio(path: str) -> dict[str, object]:
    waveform, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    return {
        "waveform": torch.from_numpy(waveform.T),
        "sample_rate": sample_rate,
    }


def main() -> None:
    audio_path = sys.argv[1] if len(sys.argv) > 1 else "/audio/test_10s.wav"

    print("=" * 60)
    print("[setup] torch.cuda.is_available =", torch.cuda.is_available())
    print("[setup] device name             =", torch.cuda.get_device_name(0))

    print("\n[A] loading pipeline (no .to)")
    from pyannote.audio import Pipeline
    pipe = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1", token=_find_token()
    )

    print("[A] params BEFORE .to(cuda):", _device_summary(pipe))
    print("[A] cuda mem alloc           :", torch.cuda.memory_allocated() // (1024 * 1024), "MiB")

    print("\n[B] moving to cuda")
    pipe.to(torch.device("cuda"))
    torch.cuda.synchronize()
    print("[B] params AFTER .to(cuda) :", _device_summary(pipe))
    print("[B] cuda mem alloc          :", torch.cuda.memory_allocated() // (1024 * 1024), "MiB")
    cuda_mem_loaded = torch.cuda.memory_allocated()

    audio = _load_audio(audio_path)
    print(f"\n[C] audio loaded: shape={tuple(audio['waveform'].shape)} sr={audio['sample_rate']}")

    print("\n[D] CUDA inference (warmup + timed)")
    _ = pipe(audio)  # warmup
    torch.cuda.synchronize()
    pre_alloc = torch.cuda.memory_allocated()
    pre_peak = torch.cuda.max_memory_allocated()
    t0 = time.perf_counter()
    out_cuda = pipe(audio)
    torch.cuda.synchronize()
    cuda_secs = time.perf_counter() - t0
    post_peak = torch.cuda.max_memory_allocated()
    print(f"[D] cuda took = {cuda_secs:.2f}s")
    print(f"[D] cuda peak alloc during run = {post_peak // (1024 * 1024)} MiB"
          f" (delta {(post_peak - pre_alloc) // (1024 * 1024)} MiB over baseline)")

    print("\n[E] moving to CPU and timing identical inference")
    pipe.to(torch.device("cpu"))
    torch.cuda.synchronize()
    cuda_mem_after_cpu = torch.cuda.memory_allocated()
    print(f"[E] cuda mem after .to(cpu) = {cuda_mem_after_cpu // (1024 * 1024)} MiB"
          f" (loaded was {cuda_mem_loaded // (1024 * 1024)} MiB — should drop to ~0)")
    t0 = time.perf_counter()
    out_cpu = pipe(audio)
    cpu_secs = time.perf_counter() - t0
    print(f"[E] cpu took  = {cpu_secs:.2f}s")

    print("\n[F] verdict")
    print(f"    cuda inference: {cuda_secs:.2f}s")
    print(f"    cpu  inference: {cpu_secs:.2f}s")
    if cpu_secs > cuda_secs * 1.5:
        ratio = cpu_secs / cuda_secs
        print(f"    CPU is {ratio:.1f}x slower → GPU was REAL ✅")
    elif cuda_secs > cpu_secs * 1.2:
        print("    GPU run was SLOWER than CPU — GPU likely fell back to CPU ❌")
    else:
        print("    GPU and CPU times are similar — small workload, GPU underutilized")

    # Don't unused-variable warn
    _ = out_cuda
    _ = out_cpu


if __name__ == "__main__":
    main()
