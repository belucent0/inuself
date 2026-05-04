"""insanely-fast-whisper smoke test in WSL ROCm container.

Uses the underlying transformers pipeline directly (the CLI wraps the same
code) so we can pre-decode audio with soundfile and avoid torchcodec.
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


def main() -> None:
    audio_path = sys.argv[1] if len(sys.argv) > 1 else "/audio/test_10s.wav"
    model_id = os.getenv("WHISPER_MODEL", "openai/whisper-large-v3-turbo")

    print("=" * 60)
    print(f"[setup] torch.cuda.is_available = {torch.cuda.is_available()}")
    print(f"[setup] device                  = {torch.cuda.get_device_name(0)}")
    print(f"[setup] model                   = {model_id}")
    print(f"[setup] audio                   = {audio_path}")

    # Load audio with soundfile, resample to 16kHz mono if needed
    waveform, sr = sf.read(audio_path, dtype="float32", always_2d=False)
    if waveform.ndim > 1:
        waveform = waveform.mean(axis=1)
    if sr != 16000:
        # whisper expects 16kHz — quick resample via torchaudio
        import torchaudio.functional as F
        waveform = F.resample(
            torch.from_numpy(waveform).unsqueeze(0), sr, 16000
        ).squeeze(0).numpy()
        sr = 16000
    print(f"[audio] {waveform.shape[0]} samples @ {sr}Hz = {waveform.shape[0]/sr:.2f}s")

    print("\n[load] importing transformers pipeline")
    t0 = time.perf_counter()
    from transformers import pipeline
    pipe = pipeline(
        "automatic-speech-recognition",
        model=model_id,
        torch_dtype=torch.float16,
        device="cuda:0",
        token=_find_token(),
    )
    print(f"[load] pipeline ready in {time.perf_counter() - t0:.1f}s")

    # Warmup — use return_timestamps=True to support long-form (>30s) audio
    print("\n[warmup] one short forward pass")
    t0 = time.perf_counter()
    _ = pipe({"array": waveform, "sampling_rate": sr}, return_timestamps=True)
    print(f"[warmup] took {time.perf_counter() - t0:.2f}s")

    # Timed
    print("\n[timed] real transcription")
    t0 = time.perf_counter()
    result = pipe(
        {"array": waveform, "sampling_rate": sr},
        chunk_length_s=30,
        batch_size=8,
        return_timestamps=True,
    )
    elapsed = time.perf_counter() - t0
    print(f"[timed] took {elapsed:.2f}s (audio {waveform.shape[0]/sr:.2f}s → RTF {elapsed/(waveform.shape[0]/sr):.2f}x)")

    print("\n=== TRANSCRIPTION ===")
    text = result.get("text", "") if isinstance(result, dict) else str(result)
    print(text[:500])
    chunks = result.get("chunks", []) if isinstance(result, dict) else []
    if chunks:
        print(f"\n=== CHUNKS ({len(chunks)}) ===")
        for c in chunks:
            ts = c.get("timestamp", (None, None))
            print(f"  {ts[0]}->{ts[1]}: {c.get('text','')[:120]}")

    # Save JSON for downstream comparison
    import json
    out_path = "/results/whisper.json"
    if os.path.isdir("/results"):
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"text": text, "chunks": chunks, "rtf": elapsed/(waveform.shape[0]/sr)}, f, ensure_ascii=False, indent=2)
        print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    main()
