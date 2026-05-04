"""Pyannote speaker-diarization smoke test in WSL ROCm container.

Verifies the same path as the host scripts/diarization_server.py works
inside a container with the librocdxg + amdsmi-fake stubs. Reads an
audio file (default /audio/test_10s.wav) and prints diarization turns.
"""
from __future__ import annotations

import os
import sys
import time

import torch


def find_token() -> str | None:
    """Try env vars then HF cache file."""
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

    print(f"[1/4] torch.cuda.is_available() = {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"      device 0: {torch.cuda.get_device_name(0)}")

    hf_token = find_token()
    print(f"[2/4] HF token: {'set (' + hf_token[:6] + '...)' if hf_token else 'MISSING'}")
    print(f"      audio file: {audio_path} ({os.path.getsize(audio_path) // 1024} KB)")

    # Production default: community-1 (2025-09 release). 5% faster + finer
    # segmentation + better DER than 3.1, drop-in compatible.
    model_id = os.getenv("DIAR_MODEL", "pyannote/speaker-diarization-community-1")
    print(f"[3/4] loading diarization pipeline ({model_id})")
    t0 = time.perf_counter()
    from pyannote.audio import Pipeline
    pipe = Pipeline.from_pretrained(
        model_id,
        token=hf_token,
    )
    if torch.cuda.is_available():
        pipe.to(torch.device("cuda"))
    print(f"      load+to(cuda) took {time.perf_counter() - t0:.1f}s")

    print("[4/4] running diarization")
    # pyannote.audio 4.x defaults to torchcodec, which is ABI-incompatible with
    # this image's ROCm torch. Preload audio with soundfile into the in-memory
    # dict format pyannote accepts to bypass torchcodec entirely.
    import soundfile as sf
    waveform, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
    # soundfile gives (time, channel); pyannote wants (channel, time)
    waveform_t = torch.from_numpy(waveform.T)
    audio_input = {"waveform": waveform_t, "sample_rate": sample_rate}
    t0 = time.perf_counter()
    out = pipe(audio_input)
    elapsed = time.perf_counter() - t0
    print(f"      diarization took {elapsed:.2f}s")

    print("=== SPEAKER TURNS ===")
    # pyannote 4.x returns DiarizeOutput with .speaker_diarization Annotation
    annotation = getattr(out, "speaker_diarization", out)
    n = 0
    speakers: set[str] = set()
    total_speech = 0.0
    for turn, _track, speaker in annotation.itertracks(yield_label=True):
        print(f"  {turn.start:6.2f}s -> {turn.end:6.2f}s  speaker={speaker}")
        speakers.add(str(speaker))
        total_speech += turn.end - turn.start
        n += 1
    print(f"=== {n} turns, {len(speakers)} unique speakers, {total_speech:.2f}s total speech ===")
    print(f"=== diarization wall: {elapsed:.2f}s ===")

    # Save JSON for downstream comparison
    import json
    if os.path.isdir("/results"):
        segs = []
        for turn, _track, speaker in annotation.itertracks(yield_label=True):
            segs.append({"start": float(turn.start), "end": float(turn.end), "speaker": str(speaker)})
        # Output filename per model
        safe_name = model_id.replace("/", "_").replace("-", "_")
        out_path = f"/results/{safe_name}.json"
        audio_dur = waveform_t.shape[1] / sample_rate if 'waveform_t' in dir() else 0
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"segments": segs, "speakers": list(speakers), "rtf": elapsed/audio_dur if audio_dur else None}, f, ensure_ascii=False, indent=2)
        print(f"\n[saved] {out_path}")


if __name__ == "__main__":
    main()
