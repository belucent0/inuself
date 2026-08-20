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

## 26B A4B AWQ/MTP feasibility (2026-08-12)

Target `cyankiwi/gemma-4-26B-A4B-it-qat-AWQ-INT4` and the official
`google/gemma-4-26B-A4B-it-qat-q4_0-unquantized-assistant` loaded successfully
with vLLM 0.26. The test used 8K context, 1 GiB fixed KV cache, four sequences,
eager mode, and a fixed 40-input/128-output-token greedy prompt. One serial and
one concurrent-four warm-up were discarded before measurement.

| MTP k | serial aggregate tok/s | concurrent-4 aggregate tok/s | approximate draft acceptance |
|---:|---:|---:|---:|
| 0 | 6.48 | 25.69 | — |
| 1 | 11.28 | 39.12 | 75% |
| 2 | 13.03 | 42.52 | 60% |
| 3 | 15.02 | 47.37 | 55% |
| **4** | **17.10** | **52.47** | 50% |
| 5 | 13.34 | 48.95 | 39% |
| 6 | 13.93 | 45.93 | 33% |

MTP increased model memory from 16.59 GiB to 17.37 GiB; the fixed KV cache adds
1 GiB. Cached API startup took about 9.7–13.3 minutes. The serial output
hash was identical for every k. Occasional concurrent hash differences also
occurred without MTP and on the 12B model, so they were not isolated to A4B MTP.

| identical synthetic workload | serial tok/s | concurrent-4 aggregate tok/s |
|---|---:|---:|
| A4B MTP k=4, standalone | 17.10 | 52.47 |
| 12B MTP k=1, standalone | 10.56 | 18.23 |
| 12B MTP k=1, full production stack resident | 2.54 | 8.79 |

These rows are throughput comparisons, not equivalent serving-capacity profiles:
A4B used 8K/KV 1 GiB/seq4, while production 12B uses 16K/KV 4 GiB/seq12. A4B
also needs at least 5.48 GiB more model-plus-KV memory than the current 12B setup.
The 890M has no tuned `E=128,N=704` INT4 MoE configuration, so vLLM used its
default configuration. vLLM also warned that `max_num_batched_tokens=512` may
under-schedule the k=4/seq4 profile.

### A4B k=4 workload and coexistence check (2026-08-13)

The cached model was tested with normal chat requests and then kept live while
the production ASR and diarization services handled a real 10-second WAV file.

| workload | output tokens | wall time | average output tok/s | finish | result |
|---|---:|---:|---:|---|---|
| Korean-to-English translation | 17 | 35.452 s | 0.48 | stop | correct; includes first-request ROCm JIT |
| Korean summarization | 89 | 9.564 s | 9.31 | stop | followed the requested two-sentence format |
| screenshot OCR | 384 | 43.346 s | 8.86 | length | title and column names found, but numeric extraction was incomplete and truncated |

The multimodal target accepted the image, but vLLM logged that the MTP assistant
falls back to text-only for multimodal input. The OCR HTTP path works, but this
384-token result is not a production-quality OCR pass.

| resident workload | iGPU committed | WSL available | check |
|---|---:|---:|---|
| A4B k=4 | 21.886 GiB | 22.792 GiB | ready |
| A4B + Whisper v3 Turbo | 25.859 GiB | 19.450 GiB | 10-second transcription and A4B smoke passed |
| A4B + Whisper + loaded pyannote | 26.335 GiB | 18.591 GiB | diarization and A4B smoke passed |
| A4B + loaded pyannote + HIP EmbeddingGemma | 23.603 GiB | 21.505 GiB | 768-dimension embedding and A4B smoke passed after ASR stopped |

The Windows GPU counter also reported a separate, unchanged 4.602 GiB adapter
commit during every sample; it is not included in the iGPU column.

Pyannote processed the 10-second file in 13.46 seconds (`RTF=1.346`) and found
one speaker. No OOM or ROCm reset occurred. This proves short-run coexistence,
not equivalent serving capacity: A4B still used 8K/KV 1 GiB/seq4 versus the
production 12B profile at 16K/KV 4 GiB/seq12.

Keep production on 12B MTP k=1. Use A4B k=4 only as the next experimental
profile until 16K/concurrency capacity, longer soak behavior, and complete OCR
output are validated.
