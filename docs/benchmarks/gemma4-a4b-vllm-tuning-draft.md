# Gemma 4 26B A4B vLLM tuning draft

Status: deployed as the local production profile; soak validation pending
Measurements: 2026-08-12 through 2026-08-20

Prior MTP and coexistence results are documented in
[`gemma4-12b-vllm-mtp.md`](./gemma4-12b-vllm-mtp.md). This draft tracks the
32K, concurrency, KV-cache, and scheduler sweep.

## Current conclusion

The highest fully verified profile is:

```text
max-model-len=32768
max-num-seqs=4
kv-cache-memory=3 GiB
max-num-batched-tokens=1024
MTP k=4
```

It ran with Whisper v3 Turbo vLLM, pyannote, and HIP EmbeddingGemma resident.
This profile replaces Gemma 4 12B as the local GPU default. The incremental
sweep below identifies batch1536 and seq8 as independent throughput candidates,
and keeps 32K as the default length because 48K/64K prefill is too slow. The
combined seq8/batch1536 profile was slower and used more memory than
seq8/batch1024, so it was rejected.

## Production validation (2026-08-15)

| Check | Result |
|---|---|
| Loaded model | `gemma4-a4b`, 32K / seq4 / KV 3 GiB / batch 1024 / MTP k4 |
| Direct multimodal OCR | 330 prompt + 1,024 completion tokens in 283.30 s; expected Korean table text and `Gemma 4` recognized |
| Gateway chat | `gemma4-a4b` returned the exact `A4B_READY` marker |
| Gateway OCR | HTTP 200; model comparison table and `Gemma 4` recognized through port 4000 |
| First concurrent auxiliary work | ASR 225 characters, pyannote 11 segments at RTF 4.349, embedding 768 dimensions |
| Full-stack overlap | 162.636 s; peak 30.195 GiB, no swap |
| Warm pyannote | 5.63 s on a 33.54 s sample (RTF 0.168) |
| Warm concurrent ASR + pyannote | 129.169 s; pyannote RTF 3.469, peak 30.191 GiB |
| Production serialized ASR → pyannote | 25.69–27.27 s warm; ASR 19.83–20.00 s + pyannote 5.68–7.43 s |
| Live A4B child-failure drill | Dependents stopped before reload; full chain recovered in about 14 min 21 s, peak 30.172 GiB, no swap |

The OCR request alone peaked at 27.545 GiB. The concurrent auxiliary run is the
observed high-water mark and leaves about 0.6 GiB below the experimental 30.8
GiB stop, so the one-hour soak remains required. Even after warm-up, running
Whisper and pyannote together made the 33.54-second sample take 129.169 seconds.
The production worker therefore keeps one GPU lock and runs ASR before pyannote;
the same sample completed in 25.69–27.27 seconds warm. After an ASR container
restart, its first serialized request took 58.20 seconds; the next took 27.27
seconds. GPU services start in the tested order: A4B, ASR, a 133.6-second real-
audio pyannote warm-up, embedding, then the gateway and request workers.

Each downstream entrypoint continues monitoring its upstream after startup.
Three consecutive health failures stop the child process so Docker restarts it
in the same A4B-first chain. A live drill terminated only the A4B vLLM child:
the LLM container stayed up, every dependent restarted once, A4B reloaded only
after the dependent endpoints were down, and the recovered model returned the
exact `A4B_READY` marker. The LLM container restart count remained zero.

## Completed matrix

| KV / batch | Effective KV | Full-stack ready | Peak iGPU | Result |
|---|---:|---:|---:|---|
| 3 GiB / 1024 | 80,972 tokens | 27.464 GiB | 29.436 GiB | PASS under the 30.5 GiB continuation rule |
| 3 GiB / 2048 | 61,260 tokens | not retained | 30.264 GiB | production-matched four-16K completed; reject |
| 4 GiB / 2048 | 81,676 tokens | 29.433 GiB | 31.36 GiB | preliminary runner stopped; reject |

The 4 GiB run predates the production-matched multimodal harness and is retained
only as rejection evidence; its KV capacity is not compared with the current
3 GiB result. It crossed the 30.8 GiB test stop. The stop is polling-based and
observed the peak after it occurred; it is not an OOM prevention guarantee.

### Incremental limit sweep (2026-08-15)

All rows kept KV 3 GiB and MTP k4. Each independent row changed only the named
setting from the production 32K / seq4 / batch1024 profile and overlapped real
ASR, pyannote, and embedding stress.

| Candidate | Effective KV / peak | Verified result | Decision |
|---|---|---|---|
| batch1536, 32K, seq4 | 69,750 tokens / 30.185 GiB | four-16K: 426.8 s, 1.200 tok/s; warm short-four: 11.092 tok/s | PASS independently: about 10.6% higher long aggregate throughput than batch1024 |
| batch2048, 32K, seq4 | 61,260 tokens / 30.264 GiB | four-16K: 506.1 s, 1.012 tok/s; warm short-four: 3.904 tok/s | Reject: slower than batch1024 and insufficient KV for all 63,284 request tokens at once |
| seq8, 32K, batch1024 | at least 63,592 request tokens / 30.405 GiB | warm short-six: 24.026 tok/s; warm short-eight: 34.306 tok/s; eight-8K: 312.8 s, 3.274 tok/s | PASS; strongest throughput candidate, but only 0.395 GiB below the 30.8 stop |
| seq8 + batch1536, 32K | 69,750 tokens / 30.568 GiB | warm short-eight: 24.609 tok/s; eight-8K: 322.2 s, 3.178 tok/s | Reject: 28.3% slower short throughput, 2.9% slower long throughput, and higher peak than seq8/batch1024 |
| 64K server, seq4, batch1024 | 106,906 tokens / 30.444 GiB | 47,500-token prompt: 778.1 s and normal stop; 63,500-token prompt exceeded the 900 s client timeout | Keep 48K only as an optional long-context ceiling; reject 64K for operation |
| 48K + seq8, batch1024 | 96,594 tokens / 30.346 GiB | 47,500-token prompt: 722.4 s; warm short-eight: 13.475 tok/s | Functional, but do not make default: observed short-request throughput regressed versus 32K/seq8 |

No sweep used swap or restarted the test server. Production was restored to
32K / seq4 / batch1024 after every profile and verified with a real completion.
The combined seq8/batch1536 profile stayed below the hard stop but regressed in
both short and long throughput. Use seq8/batch1024 as the throughput candidate.
Promotion still requires a one-hour soak and a repeated warm short-eight
measurement.

### Single-request comparison (2026-08-16)

Both profiles used the same 26-token prompt, forced 128-token output, 32K
length, 3 GiB KV cache, MTP k4, and full resident GPU stack. One warm-up was
discarded before three isolated measurements. The auxiliary column overlaps
one additional request each to ASR, pyannote, and embedding as a worst-case
stress test.

| Profile | Isolated runs | Mean | Auxiliary stress | Peak iGPU |
|---|---:|---:|---:|---:|
| seq8 / batch1024 | 5.477, 6.679, 6.049 tok/s | 6.068 tok/s | 5.312 tok/s | 30.326 GiB |
| seq4 / batch1536 | 5.515, 5.108, 5.618 tok/s | 5.414 tok/s | 5.243 tok/s | 30.185 GiB |

seq8/batch1024 was 12.1% faster for the isolated single request. Under the
worst-case auxiliary overlap the difference narrowed to 1.3%. These are
end-to-end completion rates including HTTP, TTFT, and short prefill, not pure
decode-kernel throughput.

### Controlled GPU-residency delta (2026-08-16)

This test kept one `seq8 / batch1024 / 32K / KV 3 GiB / MTP k4` A4B process
alive throughout. It measured the same 26-input/128-output request before and
after loading the auxiliary GPU models, eliminating A4B restart and JIT
variance.

| State | Runs / rate | Mean | iGPU allocation |
|---|---:|---:|---:|
| A4B only | 23.538, 22.621, 24.072 tok/s | 23.410 tok/s | 24.56 GiB during request |
| ASR + pyannote + embedding resident and idle | 5.864, 5.820, 5.204 tok/s | 5.629 tok/s | 28.17 GiB during request |
| All auxiliary inference active | 5.348 tok/s | 5.348 tok/s | 29.966 GiB peak |

Idle auxiliary residency reduced single-request throughput by 76.0%. Starting
actual auxiliary inference reduced it by only another 5.0% relative to the
resident-idle state. Allocation increased stepwise from 24.56 GiB to 27.53 GiB
after Whisper ASR, 27.88 GiB after pyannote, and 28.17 GiB after embedding.
This isolates the penalty to GPU residency/background runtime in this
ROCm/WSL/UMA environment rather than A4B configuration or cold-start variance.
Whisper is the largest allocation increase, but per-service TPS measurements
are still needed before assigning causality to one auxiliary service.

### CPU embedding mitigation (2026-08-19)

With the production `seq4 / batch1024 / 32K / KV 3 GiB / MTP k4` profile and
the auxiliary services resident but idle, EmbeddingGemma was moved from HIP
offload to `llama.cpp -ngl 0 --poll 0`. The model and 768-dimension response
format were unchanged.

| State | A4B 128-token runs | Mean | Embedding idle CPU |
|---|---:|---:|---:|
| HIP embedding | 11.311, 10.004, 10.016 tok/s | 10.444 tok/s | about 200% |
| CPU embedding | 15.505, 15.992, 14.988 tok/s | 15.495 tok/s | 0-0.84% |

This was a 48.4% A4B end-to-end throughput increase. Five sequential CPU
embedding requests with the same 6-token input averaged 146.5 ms
(37.1-422.3 ms including first-hit variance). The remaining ROCm services
still saturated the eight-CPU WSL
allocation, so this removes one source of spin but does not resolve the
runtime-wide HSA busy-loop behavior.

### ROCr WSL idle-poll fix (2026-08-20)

The remaining idle load was ROCr busy polling, not a CPU or application memory
leak. On WSL, `Runtime::AsyncEventsLoop` could not use interrupt-backed HSA
events and continuously polled while the GPU services were idle. Production is
temporarily pinned to local images built from the upstream merged fix
[`ROCm/rocm-systems#7898`](https://github.com/ROCm/rocm-systems/pull/7898)
(commit `46558b7`) until an official ROCm image contains it. The original report
is [`ROCm/librocdxg#60`](https://github.com/ROCm/librocdxg/issues/60).

| Full-stack idle service | Stock ROCr | Patched ROCr |
|---|---:|---:|
| A4B vLLM | about 275% | 13.79-14.17% |
| Whisper vLLM | about 305% | 13.14-14.02% |
| pyannote | about 210% | 3.67-6.47% |

The production validation used the existing A4B 32K / seq4 / KV 3 GiB / MTP
k4 profile with Whisper, pyannote, and CPU EmbeddingGemma resident. It verified
the exact loaded ROCr SHA for every GPU service, text completion, real 10-second
ASR, real diarization, a 768-dimension embedding, and gateway-routed image OCR.
Final iGPU allocation was 27.896 GiB with zero WSL swap; all core containers
were healthy with restart count zero. Roll back to the stock image tags if the
patched images are unavailable, and remove this pin after the fix appears in an
official ROCm release.

On this host, recreate the patched GPU services with both Compose files and no
build:

```powershell
docker compose -f docker-compose.yml -f docker-compose.rocr-wsl-pollfix.yml up -d --no-build ai-llm ai-asr-vllm ai-diarize
```

The override intentionally uses `pull_policy: never`: these are locally built,
host-specific interim images. The base Compose file remains buildable with the
stock runtime for clean checkouts and rollback.

### Fully verified 3 GiB / batch 1024 workload

| Workload | Input / output | Wall time | End-to-end output rate |
|---|---:|---:|---:|
| four concurrent 8K | 31,320 / 512 tokens | 195.447 s | 2.620 tok/s |
| four concurrent 16K | 62,772 / 512 tokens | 477.714 s | 1.072 tok/s |
| single 31K | 30,828 / 5 tokens | 400.051 s | not a decode TPS test |

The 2026-08-15 fair warm run measured 10.446 tok/s on short four-way requests
with 97.4% MTP draft acceptance. Four concurrent 16K requests measured 1.085
tok/s end-to-end, 472.05 seconds maximum wall time, and 73.4% draft acceptance.
Concurrent ASR took 25.906 seconds and pyannote took 9.55 seconds.

The single-31K effective input rate was 77.06 tok/s, versus 63.94 tok/s in the
earlier batch-512 run. This is a 17% wall-time reduction, not an MTP decode
speed measurement.

`max-num-seqs=4` does not guarantee four simultaneous 32K requests. The 80,972
token cache is about 2.47 times the configured 32K maximum and was verified up
to four concurrent 16K requests.

## Experiment backlog

| Priority | Candidate | Decision rule |
|---|---|---|
| P0 | Chosen seq8 profile one-hour full-stack soak | No restart, DXG error, swap growth, or response failure |
| P1 | Repeat warm seq8 short-eight | Confirm the 34.306 tok/s result and measure ASR/pyannote latency before promotion |
| P1 | Optional 48K long-context profile | Keep separate from the throughput profile; 48K requests take about 12–13 minutes |
| P1 | MTP k=2 or k=3 on long concurrent prompts | Run only if k4 draft acceptance is below 50% or scheduler admission remains the bottleneck |
| P1 | Translation and summary with natural EOS; broader OCR corpus | Verify quality, TTFT, and MTP acceptance under the chosen profile |
| P2 | Radeon 890M `E=128,N=704` INT4 MoE tuning | Benchmark only after the serving profile is stable; current vLLM uses its default MoE config |
| P2 | Disable eager mode / test graph capture | Separate experimental profile because startup memory and ROCm stability can change |

## Measurement rules

- The hard stop is 30.8 GiB iGPU committed; no next stage begins above 30.5 GiB.
- WSL and Windows available memory must remain at least 4 GiB and swap must not grow.
- Cold ROCm JIT runs are discarded for TPS comparisons.
- Short-prompt aggregate output TPS is reported separately from long-prompt
  end-to-end output throughput.
- Every concurrent stage overlaps real ASR, pyannote, and embedding requests.
- Production services are restored in LLM-first order and verified with real
  requests after every test.
