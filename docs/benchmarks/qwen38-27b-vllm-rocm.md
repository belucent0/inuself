# Qwen 27B W4A16 vLLM ROCm benchmark

Status: evaluation stopped; Gemma 4 A4B remains the local production model
Measurements: 2026-08-20 through 2026-08-21

## Conclusion

`philbert440/Qwen3.8-27B-W4A16-AWQ` loads in vLLM 0.26 on the Radeon 890M
(`gfx1150`), but it did not beat the deployed A4B profile strongly enough to
justify a migration. The best measured short-request aggregate was **4.67
tok/s at four concurrent requests**. Eight requests fell to 4.46 tok/s.

Further seq/batch expansion was stopped. Actual OCR and ASR/pyannote
coexistence were not run, so this is not a production-qualification result.

## Environment

- WSL2/DXG, 31.8 GiB usable iGPU memory
- vLLM 0.26.0, ROCm 7.2.3 with the existing ROCr polling fix
- Checkpoint: W4A16 compressed-tensors/AWQ, 18.21 GiB
- Resolved vLLM architecture: `Qwen3_5ForConditionalGeneration`
- Common profile: 32K max length, 3 GiB KV, BF16 activations, prefix cache
- Qwen-only safety ceiling: 25.3 GiB, reserving about 5.5 GiB for ASR and
  pyannote cold/overlap peaks
- TPS below is completion tokens divided by full HTTP request wall time; it is
  not kernel-only decode throughput

## Compatibility and failed startup settings

| Setting | Result |
|---|---|
| KV 1 GiB / 32K | Rejected: vLLM estimated only about 8K usable length |
| Unbounded multimodal dummy profile | Rejected: Torch SDPA attempted an impractical allocation |
| Image limit 1 at 1024x1024, `max_pixels=1003520` | Multimodal profile reduced to about 980 tokens |
| batch 512 | Rejected: Qwen GDN/Mamba aligned block size was 800, greater than scheduler batch 512 |
| batch 1024 | Loaded successfully |

## MTP

Same 65-prompt-token, 128-output-token workload; eager runner, seq2, batch1024.

| Mode | Steady runs | Mean | Peak GPU |
|---|---:|---:|---:|
| MTP k1 | 1.879 / 1.901 tok/s | **1.890** | 24.253 GiB |
| MTP off | 4.275 / 4.185 tok/s | **4.230** | 23.245 GiB |

MTP k1 was about 55% slower and consumed about 0.8 GiB more memory. The model
contains one MTP layer, so k2+ was not pursued after k1 clearly regressed.

## Long-context decode A/B

The patched candidate combined CUDA Graphs with the split-KV implementation
from vLLM PR [#45916](https://github.com/vllm-project/vllm/pull/45916), pinned
to commit `2d08f7f889131f97ce1874872bce01ae7cd3b631`. The PR is open and was used
only in a test image. vLLM issue
[#50264](https://github.com/vllm-project/vllm/issues/50264) describes the same
RDNA/Qwen hybrid-attention fallback and long-context decode slope.

| Context/output | Stock eager wall / TPS | split-KV + graph wall / TPS | Change |
|---|---:|---:|---:|
| 2K / 8 | 23.471 s / 0.341 | 22.182 s / 0.361 | +5.9% TPS |
| 2K / 64 | 37.483 s / 1.707 | 35.283 s / 1.814 | +6.3% TPS |
| 8K / 8 | 118.469 s / 0.068 | 113.670 s / 0.070 | +3.0% TPS |
| 8K / 64 | 134.007 s / 0.478 | 125.582 s / 0.510 | +6.7% TPS |

Subtracting the 8-token request wall from the 64-token request wall gives a
rough decode-only slope:

| Context | Stock eager | split-KV + graph |
|---|---:|---:|
| 2K | 4.00 tok/s | 4.27 tok/s |
| 8K | 3.60 tok/s | 4.70 tok/s |

The 8K decode slope improved about 30%, but long-request end-to-end latency
remained dominated by prefill. The patched profile peaked at 23.264 GiB.

## Concurrency sweep

Test profile: split-KV + CUDA Graphs, 32K, KV 3 GiB, seq8, batch2048, MTP off,
short 65-token prompt and 128 forced output tokens. The single warm-up is
excluded.

| Concurrent requests | Aggregate TPS | Average per request | Wall |
|---:|---:|---:|---:|
| 1 | 4.321 | 4.321 | 29.621 s |
| 2 | 2.373 | 1.188 | 107.874 s |
| 4 | **4.667** | 1.169 | 109.711 s |
| 8 | 4.461 | 0.558 | 229.539 s |

Peak GPU memory was 23.289 GiB. Four concurrent requests were the best point;
seq8 increased queue capacity but not throughput. Since aggregate throughput
did not exceed 5 tok/s, higher seq/batch tests were stopped.

## Decision

- Keep Gemma 4 A4B as the production multimodal LLM.
- Do not enable Qwen MTP on this vLLM/ROCm combination.
- Retain the Qwen result as a compatibility benchmark, not a deployable
  profile: OCR, full multimodal memory, and ASR/pyannote coexistence remain
  unverified.
- Revisit only after vLLM merges a supported RDNA split-KV path or a newer
  release materially changes Qwen hybrid-attention kernels.
