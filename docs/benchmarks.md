# Benchmarks

Measured on the reference machine (see DEVELOPMENT.md): NVIDIA RTX 2070, 8 GB.
The default model (`qwen3:4b`, `DEFAULT_MODEL` in `src/nova/llm/ollama.py`)
was chosen over qwen3:8b from these numbers.

To reproduce or extend them on any machine, ask Nova to find the best model
for this PC: the `model_scout` skill (AD-041) runs the same speed, placement
and tool-calling checks with Nova's real tools and keeps every run in
`~/.local/share/nova/benchmarks.json`.

## Models (`num_ctx` 8192, Nova's streaming harness)

| Model | Size | Gen speed | TTFT | Notes |
|---|---|---|---|---|
| **qwen3:4b** (default) | 2.5 GB | 28–31 tok/s | 6–10 s (incl. cold load) | fully VRAM-resident; native tools; thinking |
| qwen3:8b | 5.2 GB | ~5 tok/s | 29–45 s | 6.6 GB at 8192 ctx → 16%/84% CPU/GPU offload; the earlier 56.5 tok/s figure was at a smaller ctx |
| qwen2.5-coder:7b | 4.7 GB | 62 tok/s (smaller ctx) | 6.8 s | tool calls via text fallback |

Desktop baseline VRAM: about 660 MiB (1,388 MiB measured 2026-10-24 with Plasma 6.8 and apps open).

## qwen3:4b context sizes (2026-10-24)

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
