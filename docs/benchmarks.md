# Benchmarks

Measured on the reference machine (see DEVELOPMENT.md): NVIDIA RTX 2070, 8 GB.
The default model (`qwen3.5:4b`, `DEFAULT_MODEL` in `src/nova/llm/ollama.py`,
AD-043) was chosen from the 2026-10-28 run below; `qwen3:4b` before it was
chosen over qwen3:8b from the 2026-09-05 numbers further down.

## Candidates for 8 GB (2026-10-27/28, Ollama 0.32.14)

Each model: one `/api/chat` with a ~2,400-token prompt at `num_ctx` 8192 and
16384 (`think: false`, temperature 0, 256 generated tokens, model unloaded
first), then 12 tool-calling cases sent with Nova's real tool schemas (29) and
system prompt at 8192 (temperature 0, seed 1, no `think` parameter, as Nova
sends it), then a follow-up that must quote a number from a `linux.gpu_info`
result. "Median / max" is the time to the model's first decision (tool call or
answer). VRAM is `nvidia-smi` total, desktop included (~1.5 GB).

| Model | Download | Tools | Median / max | Gen tok/s 8K / 16K | On GPU 8K / 16K | VRAM 8K / 16K (MiB) | Uses tool result |
|---|---|---|---|---|---|---|---|
| **qwen3.5:4b** (default) | 3.3 GB | 12/12 | 2.6 / 3.8 s | 71.7 / 72.2 | 100% / 100% | 5431 / 5679 | yes |
| gemma4:e4b | 6.6 GB | 12/12 | 2.3 / 3.8 s | 91.0 / 86.9 | 100% / 100% | 6143 / 6287 | yes |
| ornith:9b | 5.6 GB | 12/12 | 2.2 / 5.5 s | 51.1 / 51.8 | 100% / 100% | 6389 / 6653 | yes |
| qwen3:4b (previous default) | 2.5 GB | 12/12 | 7.4 / 32.4 s | 81.3 / 81.1 | 100% / 100% | 5371 / 6539 | yes |
| qwen3.5:9b | 6.6 GB | 11/12 | 7.1 / 12.2 s | 14.5 / 10.5 | 74% / 70% | 6959 / 6935 | no (empty answer) |
| qwen3:8b | 5.2 GB | 11/12 | 24.4 / 57.8 s | 13.6 / 7.9 | 82% / 69% | 6771 / 6691 | yes |
| ministral-3:8b | 6.0 GB | 10/12 | 3.4 / 21.8 s | 9.1 / 5.8 | – / 56% | – / 6565 | yes |
| granite4:3b | 2.1 GB | 9/12 | 0.6 / 2.1 s | 99.2 / 100.3 | 100% / 100% | 3954 / 4605 | no (misread it) |
| qwen2.5-coder:7b | 4.7 GB | 2/12 native | 1.1 / 4.7 s | 64.8 / 65.4 | 100% / 100% | 6347 / 6803 | yes |

Notes:

- gemma4:e4b's 6.6 GB download includes vision and audio parts; `/api/ps`
  reports a bogus size for it, so its placement comes from `nvidia-smi`.
- qwen3.5:4b's first load right after the download took 22 s (147 prompt
  tok/s); measured again warm it matched the 16K row.
- qwen2.5-coder:7b writes tool calls as JSON text: Nova's text-mode fallback
  catches some of them, the native score above doesn't.
- tev1:4b and bonsai-2:9b need Ollama ≥ 0.35 and are not measured yet.

**Re-run with the GitHub MCP server's tools in the prompt** (49 tools, as the
app sends them with the server enabled), `num_ctx` 12288, 2026-10-28; "vague"
= four phrasings like "give me a github report" that should reach
`git_companion`:

| Model | Tools | Median / max | Vague git requests → skill | Loaded size |
|---|---|---|---|---|
| **qwen3.5:4b** | 12/12 | 2.4 / 6.0 s | 4/4 | 3.2 GiB |
| ornith:9b | 12/12 | 2.0 / 9.1 s | 2/4 | 5.2 GiB |
| gemma4:e4b | 12/12 | 3.0 / 8.1 s | 3/4 (asks back) | – (bogus `/api/ps` size) |

The `model_scout` skill's own run on 2026-10-28 (Nova's tools only, 32 with
memory tools) ranked gemma4:e4b (2.2 s) ahead of qwen3.5:4b (3.2 s) and
qwen3:4b (8.5 s, 11/12) and dropped qwen3:8b (80% on the GPU).

## Earlier runs

### Models (2026-09-05, `num_ctx` 8192, Nova's streaming harness)

| Model | Size | Gen speed | TTFT | Notes |
|---|---|---|---|---|
| **qwen3:4b** (default then) | 2.5 GB | 28–31 tok/s | 6–10 s (incl. cold load) | fully VRAM-resident; native tools; thinking |
| qwen3:8b | 5.2 GB | ~5 tok/s | 29–45 s | 6.6 GB at 8192 ctx → 16%/84% CPU/GPU offload; the earlier 56.5 tok/s figure was at a smaller ctx |
| qwen2.5-coder:7b | 4.7 GB | 62 tok/s (smaller ctx) | 6.8 s | tool calls via text fallback |

Desktop baseline VRAM: about 660 MiB (1,388 MiB measured 2026-10-24 with Plasma 6.8 and apps open).

### qwen3:4b context sizes (2026-10-24)

`/api/chat`, `think: false`, temperature 0, 256 generated tokens, context
filled to ~92% with chat-like text, model unloaded between sizes. Script:
throwaway, in the session scratchpad.

| num_ctx | Ollama size | GPU total | Placement | Prompt tok/s | Gen tok/s |
|---|---|---|---|---|---|
| 8192 (current) | 3.9 GB | 5.2 GB | 100% GPU | 2,127 | 77–78 |
| 12288 | 4.5 GB | 5.8 GB | 100% GPU | 1,919 | 66–70 |
| 16384 | 5.1 GB | 6.4 GB | 100% GPU | 1,741 | 63 |
| 24576 | 6.7 GB | 6.8 GB | 17% CPU / 83% GPU | 1,249 | **5.6** |
| 32768 | 7.6 GB | 7.0 GB | 29% CPU / 71% GPU | 899 | **2.1** |

16K is the largest size that stays on the GPU (1.8 GB headroom). At 16K the
history budget grows from ~2,000 to ~8,200 tokens. The gen speeds here are far
above the 28–31 tok/s recorded 2026-09-05 (that run's method is not recorded;
this one has thinking off), so compare only within this table.
