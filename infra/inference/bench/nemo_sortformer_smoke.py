"""NeMo Sortformer diarization smoke test."""
from __future__ import annotations

import os
import sys
import time
import wave


def main() -> None:
    audio_path = sys.argv[1] if len(sys.argv) > 1 else "/audio/test_10s.wav"
    model_id = os.getenv("NEMO_MODEL", "nvidia/diar_sortformer_4spk-v1")

    with wave.open(audio_path, "rb") as wf:
        duration = wf.getnframes() / wf.getframerate()
    print(f"[setup] audio = {audio_path} ({duration:.2f}s)")
    print(f"[setup] model = {model_id}")

    import torch
    print(f"[setup] cuda = {torch.cuda.is_available()} | device = {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}")

    print("\n[load] importing NeMo")
    t0 = time.perf_counter()
    from nemo.collections.asr.models import SortformerEncLabelModel
    print(f"[load] import took {time.perf_counter() - t0:.1f}s")

    print("[load] downloading + loading model")
    t0 = time.perf_counter()
    model = SortformerEncLabelModel.from_pretrained(model_id)
    if torch.cuda.is_available():
        model = model.to("cuda")
    model.eval()
    print(f"[load] ready in {time.perf_counter() - t0:.1f}s")

    print("\n[run] diarize")
    t0 = time.perf_counter()
    pred = model.diarize(audio=[audio_path], batch_size=1)
    elapsed = time.perf_counter() - t0
    print(f"[run] took {elapsed:.2f}s | RTF {elapsed/duration:.3f}x")

    # pred is a list of per-file results, each is a list of "start end speaker" strings
    segs = []
    for line in pred[0]:
        parts = line.strip().split()
        if len(parts) >= 3:
            segs.append({"start": float(parts[0]), "end": float(parts[1]), "speaker": parts[2]})

    speakers = sorted({s["speaker"] for s in segs})
    print(f"\n=== {len(segs)} turns, {len(speakers)} speakers ===")
    for s in segs[:30]:
        print(f"  {s['start']:6.1f} -> {s['end']:6.1f}  spk={s['speaker']}")
    if len(segs) > 30:
        print(f"  ... ({len(segs) - 30} more)")

    import json
    if os.path.isdir("/results"):
        out = f"/results/nemo_sortformer.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"segments": segs, "speakers": list(speakers), "rtf": elapsed/duration, "model": model_id}, f, ensure_ascii=False, indent=2)
        print(f"\n[saved] {out}")


if __name__ == "__main__":
    main()
