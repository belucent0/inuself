"""senko speaker diarization smoke test (CPU mode).

Runs senko's full pipeline (Silero VAD + Fbank + CAM++ embeddings + clustering)
on an audio file and reports RTF, number of speakers, and turn segments.
"""
from __future__ import annotations

import os
import sys
import time
import wave


def main() -> None:
    audio_path = sys.argv[1] if len(sys.argv) > 1 else "/audio/test_10s.wav"

    with wave.open(audio_path, "rb") as wf:
        duration = wf.getnframes() / wf.getframerate()
    print(f"[setup] audio = {audio_path} ({duration:.2f}s)")
    print(f"[setup] device = CPU (senko nvidia optional deps not installed)")

    # Replace silero-vad's read_audio with a soundfile-only implementation —
    # newer torchaudio (2.9) routes load() through torchcodec which we don't
    # have, so silero's stock read_audio fails. Patch BOTH submodule and
    # top-level export, plus inject torchaudio.list_audio_backends fallback.
    import soundfile as _sf
    import torch as _torch
    import torchaudio as _ta
    if not hasattr(_ta, "list_audio_backends"):
        _ta.list_audio_backends = lambda: ["soundfile"]
    def _patched_read_audio(path, sampling_rate=16000):
        data, sr = _sf.read(path, dtype="float32", always_2d=True)
        if data.shape[1] > 1:
            data = data.mean(axis=1, keepdims=True)
        wav = _torch.from_numpy(data[:, 0])
        if sr != sampling_rate:
            import torchaudio.functional as F
            wav = F.resample(wav.unsqueeze(0), sr, sampling_rate).squeeze(0)
        return wav
    import silero_vad
    import silero_vad.utils_vad as _svu
    silero_vad.read_audio = _patched_read_audio
    _svu.read_audio = _patched_read_audio

    device = os.getenv("SENKO_DEVICE", "cpu")
    print(f"\n[load] importing senko + warmup (device={device})")
    t0 = time.perf_counter()
    import senko
    diarizer = senko.Diarizer(device=device, warmup=True, quiet=False)
    print(f"[load] ready in {time.perf_counter() - t0:.1f}s")
    # Report which torch device the embedding model actually landed on
    try:
        em = getattr(diarizer, "embedding_model", None) or getattr(diarizer, "_embedding_model", None)
        if em is not None:
            for p in em.parameters():
                print(f"[load] embedding_model first param device: {p.device}")
                break
    except Exception:
        pass

    print("\n[run] diarize")
    t0 = time.perf_counter()
    result = diarizer.diarize(audio_path, generate_colors=False)
    elapsed = time.perf_counter() - t0
    print(f"[run] took {elapsed:.2f}s | RTF {elapsed/duration:.2f}x")

    segments = result.get("merged_segments", [])
    speakers = sorted({s.get("speaker") for s in segments})
    total_speech = sum(s.get("end", 0) - s.get("start", 0) for s in segments)

    print(f"\n=== {len(segments)} turns, {len(speakers)} speakers, {total_speech:.2f}s speech ===")
    for s in segments:
        st = s.get("start", 0)
        en = s.get("end", 0)
        sp = s.get("speaker")
        print(f"  {st:6.2f}s -> {en:6.2f}s  speaker={sp}")

    # Save JSON for downstream comparison
    import json
    if os.path.isdir("/results"):
        out_path = "/results/senko.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"segments": segments, "speakers": list(speakers), "rtf": elapsed/duration}, f, ensure_ascii=False, indent=2)
        print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    main()
