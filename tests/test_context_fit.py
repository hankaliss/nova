"""Tests for automatic context sizing (nova.llm.context_fit).

The fake provider reproduces placements measured on an RTX 2070 (8 GB) on
2026-10-24: Ollama keeps a model fully on the GPU while its size stays under
roughly 5.05 GB with ~6.7 GB free.
"""

import pytest

from nova.llm.context_fit import (
    LADDER,
    first_guess,
    fit_context,
    kv_bytes_per_token,
    rungs_up_to,
    step_down,
)

QWEN3_INFO = {
    "general.architecture": "qwen3",
    "qwen3.block_count": 36,
    "qwen3.attention.head_count_kv": 8,
    "qwen3.attention.key_length": 128,
}


# Ollama-reported sizes (MiB) measured on the RTX 2070, 2026-10-24.
MEASURED = {
    "qwen3:4b": {2048: 2740, 4096: 3030, 8192: 3693, 12288: 4277, 16384: 4861, 24576: 6071},
    "qwen2.5-coder:7b": {2048: 4414, 4096: 4528, 8192: 4756, 12288: 4984, 16384: 5543},
    "qwen3:8b": {2048: 5029, 4096: 5697},
}
PROFILES = {
    "qwen3:4b": (2_497_293_931, 147456, 262144),
    "qwen2.5-coder:7b": (4_683_087_561, 57344, 32768),
    "qwen3:8b": (5_225_388_164, 147456, 40960),
}


class FakeOllama:
    """Replays measured sizes; the model fits while size <= limit_mib
    (Ollama spilled every model above ~5.05 GB with ~6.7 GB free)."""

    def __init__(self, model, limit_mib=5050):
        weights, kv, max_ctx = PROFILES[model]
        self.profile = {"size": weights, "kv_bytes_per_token": kv, "context_length": max_ctx}
        self.sizes = MEASURED[model]
        self.kv = kv
        self.limit = limit_mib
        self.loads = []

    def size_mib(self, num_ctx):
        if num_ctx in self.sizes:
            return self.sizes[num_ctx]
        top = max(self.sizes)                      # extrapolate past measurements
        return self.sizes[top] + self.kv * (num_ctx - top) // 2**20

    async def model_profile(self, model):
        return self.profile

    async def load(self, model, num_ctx):
        self.loads.append(num_ctx)
        size = self.size_mib(num_ctx)
        return size * 2**20, min(size, self.limit) * 2**20


async def free_6711():
    return 6711


def test_kv_bytes_per_token_from_model_info():
    assert kv_bytes_per_token(QWEN3_INFO) == 147456          # 144 KiB, matches measurements
    assert kv_bytes_per_token({
        "general.architecture": "qwen2",
        "qwen2.block_count": 28, "qwen2.attention.head_count_kv": 4,
        "qwen2.embedding_length": 3584, "qwen2.attention.head_count": 28,
    }) == 57344
    assert kv_bytes_per_token({}) is None


def test_rungs_respect_trained_context():
    assert rungs_up_to(None) == list(LADDER)
    assert rungs_up_to(32768)[-1] == 32768
    assert rungs_up_to(4096) == [2048, 4096]
    assert rungs_up_to(1000) == [2048]


@pytest.mark.asyncio
@pytest.mark.parametrize("model,expected", [
    ("qwen3:4b", 16384),
    ("qwen2.5-coder:7b", 12288),
    ("qwen3:8b", 2048),
])
async def test_fit_picks_largest_size_that_stays_on_gpu(model, expected):
    ollama = FakeOllama(model)
    result = await fit_context(ollama, model, free_6711)
    assert result.num_ctx == expected
    assert result.on_gpu
    assert ollama.loads[-1] == expected          # left loaded at the chosen size
    assert len(ollama.loads) <= 5


@pytest.mark.asyncio
async def test_fit_climbs_when_ollama_allows_more():
    ollama = FakeOllama("qwen3:4b", limit_mib=6200)
    result = await fit_context(ollama, "qwen3:4b", free_6711)
    assert result.num_ctx == 24576 and result.on_gpu


@pytest.mark.asyncio
async def test_fit_steps_down_when_the_guess_spills():
    # Something else grabbed GPU memory since the reading was taken.
    ollama = FakeOllama("qwen3:4b", limit_mib=4000)
    result = await fit_context(ollama, "qwen3:4b", free_6711)
    assert result.num_ctx == 8192 and result.on_gpu


@pytest.mark.asyncio
async def test_fit_without_gpu_reading_starts_at_8k():
    async def unknown():
        return None

    ollama = FakeOllama("qwen3:4b")
    result = await fit_context(ollama, "qwen3:4b", unknown)
    assert ollama.loads[0] == 8192 and result.num_ctx == 16384


@pytest.mark.asyncio
async def test_fit_reports_when_nothing_fits():
    ollama = FakeOllama("qwen3:8b", limit_mib=4000)
    result = await fit_context(ollama, "qwen3:8b", free_6711)
    assert result.num_ctx == 2048 and result.on_gpu is False


def test_first_guess_and_step_down():
    assert first_guess(2_497_293_931, 147456, 6711, list(LADDER)) == 16384
    assert step_down(16384) == 12288
    assert step_down(2048) == 2048
