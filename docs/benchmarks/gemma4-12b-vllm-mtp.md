# Gemma 4 12B vLLM ROCm/MTP benchmark

Date: 2026-08-09

## Environment

- AMD Radeon 890M (`gfx1150`), WSL2/DXG, 32 GiB WSL memory limit
- `vllm/vllm-openai-rocm:v0.26.0`
- Target: `google/gemma-4-12B-it-qat-w4a16-ct`
- Assistant: `google/gemma-4-12B-it-qat-q4_0-unquantized-assistant`
- Test limits: 4K context, one sequence, 2 GiB fixed KV cache, eager runner
- Workload: one 128-token warm-up followed by two 128-token greedy measurements

## Results

| MTP k | steady TPS | mean TPS | draft acceptance | output hash |
|---:|---:|---:|---:|---|
| 1 | 9.09 / 9.37 | **9.23** | 168 / 213 = 78.9% | `9c3507d8b58588fd` |
| 2 | 6.55 / 6.72 | 6.63 | 189 / 396 = 47.7% | `5f2a403813f76be8` |
| 3 | 8.77 / 9.52 | 9.14 | 243 / 432 = 56.3% | `9c3507d8b58588fd` |
| 4 | 7.16 / 7.01 | 7.09 | 234 / 588 = 39.8% | `c70cdf6f36869573` |

### Why k=1

`k=1` had the highest mean throughput at 9.23 TPS. `k=3` was slightly slower
at 9.14 TPS, while `k=2` and `k=4` fell to 6.63 and 7.09 TPS as lower draft
acceptance added verification overhead. Because both `k=3` and `k=4` were slower
than `k=1`, the agreed stop rule was met and `k=5`/`k=6` were not run.

The first request was excluded because it included ROCm kernel JIT.

`ignore_eos=true` was used only to keep benchmark output length fixed. The differing
hashes at k=2 and k=4 occurred in this forced continuation workload; normal OCR was
also tested to a natural stop and returned correct text.

## Qwen3-VL comparison

| Model | TPS | Measurement |
|---|---:|---|
| Qwen3-VL 4B BF16 | 6.8 | Previous single-stream benchmark |
| Qwen3-VL 4B BF16 | 6.67 | Live CI evaluation: 160 generated tokens / 24 s |
| Gemma 4 12B W4A16 + MTP k=1 | **9.23** | Benchmark mean above |

Gemma was **35.7% faster** than the 6.8 TPS Qwen baseline. The live CI workload
used different prompts, so its 6.67 TPS result is a supporting observation rather
than a controlled head-to-head benchmark; vLLM's active decode logs ranged from
7.2 to 7.9 TPS.

## Multimodal OCR

A 1970x1280 dashboard screenshot was sent as an OpenAI-compatible image data URL
while MTP k=1 was active. The server returned HTTP 200 and correctly extracted the
page heading `Dashboard`, navigation labels `Dashboard`, `Traces`, `LLM Calls`,
`Scores`, `Setup`, and chart titles `Traces`, `LLM calls`, `Scores`.

The OCR request used MTP: 37 draft tokens, 34 accepted tokens (91.9%). vLLM logs
that the assistant falls back to text-only; the Gemma 4 target still performs image
encoding and the assistant accelerates text decoding.

## Decision

Use MTP `k=1`. Reserve KV memory explicitly instead of using
`gpu_memory_utilization`, because the GPU uses shared UMA while WSL is capped at
32 GiB. Production uses a 4 GiB fixed KV cache for the existing 16K context limit.
