"""Pick the largest context size that keeps a model fully on the GPU.

A model's memory need grows with its context: weights + a KV cache of
``2 × layers × kv_heads × head_dim × 2 bytes`` per token (f16) + a small
runtime overhead. Ollama decides placement with its own, more conservative
budget (it keeps ~1.5 GB back for compute buffers, and that varies per
model), so the formula only gives a *starting guess*. The answer comes from
Ollama itself: load the model at a size and check ``/api/ps`` — fully on the
GPU means ``size_vram == size``. Climb while it fits, step down when it
spills.

Measured on an RTX 2070 (8 GB, ~1.4 GB used by the desktop), 2026-10-24:
qwen3:4b fits 16384, qwen2.5-coder:7b 12288, qwen3:8b only 2048.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

# Context sizes Nova chooses between (each a common num_ctx value).
LADDER = (2048, 4096, 8192, 12288, 16384, 24576, 32768)
# Used when there is no GPU memory reading to estimate from.
FALLBACK_START = 8192
# GPU memory Ollama keeps back beyond the model's reported size (measured
# 1.6–1.7 GB across three models; slightly low on purpose — verification
# corrects the guess either way).
OLLAMA_RESERVE_MIB = 1536
# Runtime overhead on top of weights + KV cache in Ollama's reported size.
OVERHEAD_MIB = 96
# Most loads a single fit may do (each one takes a few seconds).
MAX_LOADS = 4


def kv_bytes_per_token(model_info: dict[str, Any]) -> int | None:
    """KV-cache bytes per token from Ollama's ``/api/show`` ``model_info``."""
    arch = model_info.get("general.architecture")
    if not arch:
        return None
    layers = model_info.get(f"{arch}.block_count")
    kv_heads = model_info.get(f"{arch}.attention.head_count_kv")
    head_dim = model_info.get(f"{arch}.attention.key_length")
    if head_dim is None:
        heads = model_info.get(f"{arch}.attention.head_count")
        width = model_info.get(f"{arch}.embedding_length")
        head_dim = width // heads if heads and width else None
    if not (layers and kv_heads and head_dim):
        return None
    return 2 * int(layers) * int(kv_heads) * int(head_dim) * 2


def predicted_size_mib(weights_bytes: int, kv_per_token: int, num_ctx: int) -> int:
    return (weights_bytes + kv_per_token * num_ctx) // 2**20 + OVERHEAD_MIB


def rungs_up_to(max_ctx: int | None) -> list[int]:
    """Ladder sizes the model supports (its trained context caps it)."""
    if not max_ctx:
        return list(LADDER)
    allowed = [n for n in LADDER if n <= max_ctx]
    return allowed or [LADDER[0]]


def first_guess(
    weights_bytes: int,
    kv_per_token: int | None,
    free_vram_mib: int | None,
    rungs: list[int],
) -> int:
    """Largest rung whose predicted size fits Ollama's likely budget."""
    if free_vram_mib is None or not kv_per_token or not weights_bytes:
        return max(n for n in rungs if n <= FALLBACK_START) if rungs[0] <= FALLBACK_START else rungs[0]
    budget = free_vram_mib - OLLAMA_RESERVE_MIB
    fitting = [n for n in rungs if predicted_size_mib(weights_bytes, kv_per_token, n) <= budget]
    return fitting[-1] if fitting else rungs[0]


class FitProvider(Protocol):
    async def model_profile(self, model: str) -> dict[str, Any]: ...
    async def load(self, model: str, num_ctx: int) -> tuple[int, int]: ...


@dataclass
class FitResult:
    num_ctx: int
    on_gpu: bool
    tried: list[tuple[int, bool]] = field(default_factory=list)


async def fit_context(
    provider: FitProvider,
    model: str,
    free_vram_mib: Callable[[], Awaitable[int | None]],
) -> FitResult:
    """Find the largest ladder size at which ``model`` loads fully on the GPU.

    Leaves the model loaded at the chosen size (so the first answer doesn't
    wait for a load).
    """
    profile = await provider.model_profile(model)
    rungs = rungs_up_to(profile.get("context_length"))
    start = first_guess(
        profile.get("size", 0),
        profile.get("kv_bytes_per_token"),
        await free_vram_mib(),
        rungs,
    )
    tried: dict[int, bool] = {}

    async def fits(num_ctx: int) -> bool:
        if num_ctx not in tried:
            size, size_vram = await provider.load(model, num_ctx)
            tried[num_ctx] = size > 0 and size_vram >= size
        return tried[num_ctx]

    i = rungs.index(start)
    if await fits(rungs[i]):
        # The guess is deliberately cautious: climb while the next size fits.
        while i + 1 < len(rungs) and len(tried) < MAX_LOADS and await fits(rungs[i + 1]):
            i += 1
    else:
        while i > 0 and len(tried) < MAX_LOADS and not await fits(rungs[i - 1]):
            i -= 1
        i = max(i - 1, 0)
    best = rungs[i]
    on_gpu = tried.get(best, False)
    # The last load may have been a size that didn't fit; end on the choice.
    last_loaded = list(tried)[-1]
    if last_loaded != best:
        await provider.load(model, best)
    return FitResult(num_ctx=best, on_gpu=on_gpu, tried=list(tried.items()))


def step_down(num_ctx: int) -> int:
    """Next smaller ladder size (after a spill was seen in normal use)."""
    smaller = [n for n in LADDER if n < num_ctx]
    return smaller[-1] if smaller else LADDER[0]
