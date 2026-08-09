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

The first request was excluded because it included ROCm kernel JIT. Testing stopped
at k=4 by decision rule: k=3 and k=4 were both slower than k=1. k=5 and k=6 were
not measured.

`ignore_eos=true` was used only to keep benchmark output length fixed. The differing
hashes at k=2 and k=4 occurred in this forced continuation workload; normal OCR was
also tested to a natural stop and returned correct text.

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
